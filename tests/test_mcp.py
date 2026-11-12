"""MCP 层测试：传输、压缩、预算、管理器、注册表对接。

全程用 tests/fake_mcp_server.py 当对端，不联网、不依赖任何外部 server。
每个用例自己起进程，跑完关掉，不留给下一个用例。
"""

from __future__ import annotations

import json
import sys
import tempfile
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import miniagent.mcp.manager as mm  # noqa: E402
from miniagent.core import guard  # noqa: E402
from miniagent.mcp.client import MCPGone, MCPTimeout, StdioClient  # noqa: E402
from miniagent.mcp.manager import (  # noqa: E402
    ServerConfig,
    load_config,
    render_result,
    safe_name,
    save_config,
    tool_name,
)
from miniagent.mcp.schema import (  # noqa: E402
    apply_budget,
    compact_schema,
    signature_line,
    to_openai_tool,
    tool_tokens,
)
from miniagent.tools import DISPATCH, DANGEROUS, TOOL_SCHEMAS, execute, refresh  # noqa: E402
from miniagent.tools._util import ERR as TOOL_ERR  # noqa: E402

SERVER = str(ROOT / "tests" / "fake_mcp_server.py")
PYTHON = sys.executable


def _client(extra: int = 0, timeout: float = 10) -> StdioClient:
    return StdioClient(PYTHON, [SERVER, "--extra", str(extra)], name="fake", timeout=timeout)


# ---------------------------------------------------------------- 传输层


def test_transport() -> list[tuple[str, bool]]:
    client = _client(extra=2)
    checks: list[tuple[str, bool]] = []
    try:
        info = client.start()
        checks.append(("握手拿到 serverInfo", info.get("serverInfo", {}).get("name") == "fake"))

        tools = client.request("tools/list").get("tools") or []
        checks.append(("tools/list 有内容", len(tools) == 9))
        checks.append(("工具带 inputSchema", "inputSchema" in tools[0]))

        echo = client.request("tools/call", {"name": "echo", "arguments": {"text": "你好"}})
        checks.append(("tools/call 能拿到文本", echo["content"][0]["text"] == "你好"))

        add = client.request("tools/call", {"name": "add", "arguments": {"a": 2, "b": 40}})
        checks.append(("参数正确传到了 server", float(add["content"][0]["text"]) == 42.0))

        boom = client.request("tools/call", {"name": "boom", "arguments": {}})
        checks.append(("server 报错走 isError", boom.get("isError") is True))

        started = time.time()
        try:
            client.request("tools/call", {"name": "sleep", "arguments": {"seconds": 5}}, timeout=1)
            checks.append(("超时被拦下", False))
        except MCPTimeout:
            checks.append((f"超时被拦下（{time.time() - started:.1f}s）", time.time() - started < 2.5))

        try:
            client.request("tools/call", {"name": "die", "arguments": {}}, timeout=5)
            checks.append(("进程崩溃能感知", False))
        except MCPGone:
            checks.append(("进程崩溃能感知", True))
        time.sleep(0.3)
        checks.append(("崩溃后 alive 为假", not client.alive))
    finally:
        client.close()

    checks.append(("close 之后连接不可用", not client.alive))

    noisy = StdioClient(
        PYTHON, [SERVER, "--stderr-noise"], name="noisy", timeout=10
    )
    try:
        noisy.start()
        time.sleep(0.2)
        checks.append(("stderr 被抽干并留了几行", any("fake server" in line for line in noisy.stderr_tail)))
    finally:
        noisy.close()

    bad = StdioClient("definitely-not-a-command-xyz", [], name="bad", timeout=3)
    try:
        bad.start()
        checks.append(("命令不存在给出人话错误", False))
    except Exception as exc:  # noqa: BLE001
        checks.append(("命令不存在给出人话错误", "找不到命令" in str(exc)))
    return checks


# ---------------------------------------------------------------- 压缩与预算


def test_schema() -> list[tuple[str, bool]]:
    verbose = {
        "$schema": "http://json-schema.org/draft-07/schema#",
        "title": "click",
        "type": "object",
        "properties": {
            "element": {
                "type": "string",
                "title": "Element",
                "description": "Human readable description. " * 8,
                "default": "",
            },
            "mode": {"type": "string", "enum": ["fast", "safe", "quiet"]},
            "target": {"anyOf": [{"type": "string"}, {"type": "null"}]},
        },
        "required": ["element"],
        "additionalProperties": False,
        "definitions": {"unused": {"type": "string"}},
    }
    compacted = compact_schema(verbose)
    dumped = json.dumps(compacted, ensure_ascii=False)

    tool = to_openai_tool("mcp__x__click", "做一个很长的说明。" * 30, verbose)
    tokens = tool_tokens(tool)

    many = [to_openai_tool(f"mcp__x__t{i}", "说明", verbose) for i in range(30)]
    kept, dropped = apply_budget(many, 600)
    kept_tokens = sum(tool_tokens(t) for t in kept)
    all_kept, none_dropped = apply_budget(many, 0)
    single = apply_budget(many[:1], 1)

    return [
        ("$schema 被清掉", "$schema" not in dumped),
        ("title 被清掉", '"title"' not in dumped),
        ("additionalProperties 被清掉", "additionalProperties" not in dumped),
        ("definitions 被清掉", "definitions" not in dumped),
        ("default 被清掉", '"default"' not in dumped),
        ("枚举保留", compacted["properties"]["mode"]["enum"] == ["fast", "safe", "quiet"]),
        ("required 保留", compacted["required"] == ["element"]),
        ("anyOf 降成单一类型", compacted["properties"]["target"]["type"] == "string"),
        ("长描述被截断", len(compacted["properties"]["element"]["description"]) < 90),
        ("工具描述被截断", len(tool["function"]["description"]) <= 151),
        ("压缩后 token 明显更少", tokens < tool_tokens({"type": "function", "function": {"name": "x", "description": "y" * 600, "parameters": verbose}})),
        ("单个工具 token 可控（<200）", tokens < 200),
        ("预算生效", kept_tokens <= 600),
        ("省下的进了降级列表", len(dropped) > 0 and len(kept) + len(dropped) == 30),
        ("预算为 0 全降级", not all_kept and len(none_dropped) == 30),
        ("单个超预算也留一个", len(single[0]) == 1),
        ("签名是一行", len(signature_line(tool).splitlines()) == 1 and "(" in signature_line(tool)),
        ("签名比完整定义省得多", tool_tokens({"type": "function", "function": {"name": "x", "description": signature_line(tool), "parameters": {"type": "object", "properties": {}}}}) < tokens),
    ]


# ---------------------------------------------------------------- 管理器


def _setup_tmp(tmp: Path, extra: int = 4, tools: list[str] | None = None) -> None:
    mm.MCP_CONFIG = tmp / "mcp.json"
    mm.MCP_CACHE_DIR = tmp / "cache"
    save_config(
        [
            ServerConfig(
                name="fake",
                command=PYTHON,
                args=[SERVER, "--extra", str(extra)],
                tools=tools or [],
            )
        ]
    )


def test_manager() -> list[tuple[str, bool]]:
    checks: list[tuple[str, bool]] = []
    tmp = Path(tempfile.mkdtemp())
    original = (mm.MCP_CONFIG, mm.MCP_CACHE_DIR)
    try:
        white = ["echo", "add", *[f"noise_{i}" for i in range(20)]]
        _setup_tmp(tmp, extra=30, tools=white)
        mm.mcp_manager.reload()

        servers = mm.mcp_manager.servers()
        checks.append(("配置读到了", len(servers) == 1 and servers[0].name == "fake"))

        # 还没同步时拿不到工具：懒启动，不连也能起界面
        checks.append(("未同步时没有工具", mm.mcp_manager.usage()["injected"] == 0))
        checks.append(("懒启动：没有子进程", mm.mcp_manager.status()[0]["connected"] is False))

        message = mm.mcp_manager.sync("fake")
        checks.append(("同步成功", "个工具" in message))
        checks.append(("缓存落盘", (tmp / "cache" / "fake.json").is_file()))

        schemas, handlers = mm.mcp_manager.tools()
        names = [s["function"]["name"] for s in schemas]
        checks.append(("工具名带 mcp__ 前缀", all(n.startswith("mcp__fake__") for n in names if n != "mcp_call")))
        real = [n for n in names if n != "mcp_call"]
        checks.append((
            "白名单生效：没同步进来的不出现",
            all(n[len("mcp__fake__"):] in white for n in real),
        ))
        checks.append((f"预算把工具挡掉了（{len(real)}/22）", len(real) < 22))
        checks.append(("超预算时注册元工具", "mcp_call" in names))
        checks.append(("handler 和 schema 对得上", set(handlers) == set(names)))

        echo_name = next(n for n in names if n.endswith("echo"))
        out = handlers[echo_name](text="hi")
        checks.append(("直接调用工具通", "hi" in out))

        out = mm.mcp_manager.call("fake", "boom", {})
        checks.append(("isError 转成 [ERR]", out.startswith(ERR := "[ERR]")))

        out = mm.mcp_manager.call("nope", "echo", {})
        checks.append(("未知 server 有提示", "没有名为 nope" in out))

        out = mm.mcp_manager.call("fake", "不存在", {})
        checks.append(("未知工具回错误", out.startswith("[ERR]")))

        shot = mm.mcp_manager.call("fake", "screenshot", {})
        checks.append(("图片不进上下文", "base64" not in shot and "image/png" in shot))

        big = mm.mcp_manager.call("fake", "big", {})
        checks.append(("超长结果被截断", len(big) < 4200))

        meta = mm.mcp_manager.call_by_name("fake", "echo", '{"text":"走元工具"}')
        checks.append(("元工具能调", "走元工具" in meta))
        bad = mm.mcp_manager.call_by_name("fake", "echo", "not json")
        checks.append(("元工具坏 JSON 有提示", bad.startswith("[ERR]")))

        summary = mm.mcp_manager.summary_text()
        checks.append(("有降级摘要", "mcp_call" in summary and "mcp__fake__" in summary))

        usage = mm.mcp_manager.usage()
        checks.append(("注入量不超预算", usage["tokens"] <= usage["budget"]))
        checks.append(
            (
                "schema + 摘要一起算也不超预算",
                usage["tokens"] + usage["summary_tokens"] <= usage["budget"] + 20,
            )
        )
        full = sum(tool_tokens(t) for t in mm.mcp_manager._dropped)
        saved = 1 - usage["summary_tokens"] / max(1, full)
        checks.append((f"摘要比全量注入省 {saved:.0%}", saved > 0.6))

        status = mm.mcp_manager.status()[0]
        checks.append(("状态里能看到已连接", status["connected"] is True))
        checks.append(("状态里有工具数", status["tools"] >= 4))

        mm.mcp_manager.close_all()
        checks.append(("close_all 之后断开", mm.mcp_manager.status()[0]["connected"] is False))
    finally:
        mm.mcp_manager.close_all()
        mm.MCP_CONFIG, mm.MCP_CACHE_DIR = original
    return checks


# ---------------------------------------------------------------- 注册表


def test_registry() -> list[tuple[str, bool]]:
    checks: list[tuple[str, bool]] = []
    tmp = Path(tempfile.mkdtemp())
    original = (mm.MCP_CONFIG, mm.MCP_CACHE_DIR)
    try:
        _setup_tmp(tmp, extra=0, tools=["echo"])
        mm.mcp_manager.reload()
        mm.mcp_manager.sync("fake")
        refresh()

        names = [s["function"]["name"] for s in TOOL_SCHEMAS]
        checks.append(("MCP 工具进了注册表", any(n == "mcp__fake__echo" for n in names)))
        checks.append(("内置工具还在", "read_file" in names))
        checks.append(("MCP 工具算危险操作", "mcp__fake__echo" in DANGEROUS))
        checks.append(("DISPATCH 里有实现", "mcp__fake__echo" in DISPATCH))

        out = execute("mcp__fake__echo", '{"text":"从注册表走的"}')
        checks.append(("execute 能调到 MCP", "从注册表走的" in out))

        out = execute("mcp__fake__echo", '{"text": "带空格"}')
        checks.append(("参数解析正常", "带空格" in out))
    finally:
        mm.mcp_manager.close_all()
        mm.MCP_CONFIG, mm.MCP_CACHE_DIR = original
        refresh()  # 恢复成没有 MCP 的干净注册表
    return checks


# ---------------------------------------------------------------- 配置与命名


def test_config() -> list[tuple[str, bool]]:
    tmp = Path(tempfile.mkdtemp())
    path = tmp / "mcp.json"
    path.write_text(
        json.dumps(
            {
                "servers": [
                    {"name": "a", "command": "node", "args": ["x.js"]},
                    {"name": "a", "command": "dup"},  # 重名，跳过
                    {"name": "", "command": "node"},  # 没名字，跳过
                    {"command": "node"},  # 没名字，跳过
                    {"name": "b", "command": "python", "args": "a.py b.py", "enabled": False},
                ]
            }
        ),
        encoding="utf-8",
    )
    servers = load_config(path)

    missing = tmp / "nope.json"
    return [
        ("坏条目被跳过", len(servers) == 2),
        ("重名只留第一个", servers[0].args == ["x.js"]),
        ("字符串 args 会切开", servers[1].args == ["a.py", "b.py"]),
        ("enabled 读得到", servers[1].enabled is False),
        ("配置文件不存在返回空", load_config(missing) == []),
        ("工具名带前缀", tool_name("my server", "do it") == "mcp__my_server__do_it"),
        ("超长名被截断", len(tool_name("x" * 80, "y" * 80)) <= 60),
        ("奇怪字符被替换", safe_name("a-b.c/中") == "a_b_c"),
        ("ERR 常量三处一致", mm.ERR.strip() == TOOL_ERR.strip() == guard.ERR_PREFIX.strip()),
    ]


# ---------------------------------------------------------------- 结果渲染


def test_render() -> list[tuple[str, bool]]:
    text_only = render_result({"content": [{"type": "text", "text": "ok"}]})
    err = render_result({"content": [{"type": "text", "text": "坏了"}], "isError": True})
    image = render_result(
        {"content": [{"type": "image", "mimeType": "image/png", "data": "A" * 40000}]}
    )
    empty = render_result({"content": []})
    long_text = render_result({"content": [{"type": "text", "text": "x" * 9000}]})
    return [
        ("纯文本原样返回", text_only == "ok"),
        ("isError 加 [ERR]", err.startswith("[ERR]")),
        ("图片换成元信息", "image/png" in image and "A" * 100 not in image),
        ("空内容有占位", "无内容" in empty),
        ("超长被截断", len(long_text) < 5000),
    ]


def main() -> int:
    print("[mcp] 开始")
    checks = (
        test_config()
        + test_schema()
        + test_render()
        + test_transport()
        + test_manager()
        + test_registry()
    )
    for label, ok in checks:
        print(f"  {'PASS' if ok else 'FAIL'}  {label}")
    ok_all = all(ok for _, ok in checks)
    print(f"[mcp] {'全部通过' if ok_all else '存在失败项'}")
    return 0 if ok_all else 1


if __name__ == "__main__":
    raise SystemExit(main())
