"""MCP 管理器：配置、生命周期、工具注册。

跟插件的区别就一句话：插件是**进程内的 Python**，MCP 是**独立进程**。
所以这里多了三件插件层不需要处理的事：

1. **懒启动**。开工时不拉进程，只读磁盘缓存里的工具清单。真正要调工具才
   起子进程 —— 没配 MCP 的人启动速度一点不受影响。
2. **结果要用文本表达**。MCP 回的是 content 块（text / image / resource），
   图片是 base64。直接塞进上下文会当场烧掉几千 token，所以图片只留一句
   元信息。
3. **工具名要能看出出处**。统一 `mcp__{server}__{tool}`。

预算控制见 schema.py：不是所有工具都注入 schema，超预算的降级成一行签名，
模型看到签名还能通过 `mcp_call` 元工具调。
"""

from __future__ import annotations

import json
import re
from dataclasses import asdict, dataclass, field
from pathlib import Path

from ..config import MCP_CACHE_DIR, MCP_CONFIG, mcp_timeout, mcp_tool_budget
from ..markers import ERR
from ..tokens import estimate_text
from .client import MCPError, StdioClient
from .schema import apply_budget, signature_line, to_openai_tool, tool_tokens

NAME_SAFE = re.compile(r"[^A-Za-z0-9_]+")
MAX_NAME = 60
MAX_RESULT_CHARS = 4000
DEFAULT_MAX_RESULT = 4000


@dataclass
class ServerConfig:
    name: str
    command: str
    args: list[str] = field(default_factory=list)
    env: dict = field(default_factory=dict)
    cwd: str = ""
    enabled: bool = True
    tools: list[str] = field(default_factory=list)  # 白名单；空 = 全部
    max_result: int = DEFAULT_MAX_RESULT

    def fingerprint(self) -> str:
        """命令变了，缓存里的工具清单就作废。"""
        return json.dumps([self.command, self.args, self.cwd], ensure_ascii=False)


def safe_name(text: str) -> str:
    """工具名只留 [A-Za-z0-9_]，别让奇怪的 server 名把模型搞晕。"""
    cleaned = NAME_SAFE.sub("_", str(text or "")).strip("_")
    return cleaned or "server"


def tool_name(server: str, tool: str) -> str:
    """`mcp__{server}__{tool}`，超长截断 —— OpenAI 的函数名上限是 64。"""
    name = f"mcp__{safe_name(server)}__{safe_name(tool)}"
    return name[:MAX_NAME] if len(name) > MAX_NAME else name


# ---------------------------------------------------------------- 配置读写


def load_config(path: Path | None = None) -> list[ServerConfig]:
    target = path or MCP_CONFIG
    if not target.is_file():
        return []
    try:
        data = json.loads(target.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return []

    rows = data.get("servers") if isinstance(data, dict) else data
    if not isinstance(rows, list):
        return []

    servers: list[ServerConfig] = []
    seen: set[str] = set()
    for row in rows:
        if not isinstance(row, dict):
            continue
        name = str(row.get("name") or "").strip()
        command = str(row.get("command") or "").strip()
        if not name or not command or name in seen:
            continue  # 缺名字或缺命令的条目直接跳过，别让坏配置拖垮启动
        seen.add(name)
        args = row.get("args") or []
        if isinstance(args, str):
            args = args.split()
        servers.append(
            ServerConfig(
                name=name,
                command=command,
                args=[str(a) for a in args if str(a).strip()],
                env={str(k): str(v) for k, v in (row.get("env") or {}).items()},
                cwd=str(row.get("cwd") or ""),
                enabled=bool(row.get("enabled", True)),
                tools=[str(t) for t in (row.get("tools") or []) if str(t).strip()],
                max_result=int(row.get("max_result") or DEFAULT_MAX_RESULT),
            )
        )
    return servers


def save_config(servers: list[ServerConfig], path: Path | None = None) -> bool:
    target = path or MCP_CONFIG
    try:
        target.parent.mkdir(parents=True, exist_ok=True)
        payload = {"servers": [asdict(s) for s in servers]}
        target.write_text(
            json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
        )
        return True
    except OSError:
        return False


# ---------------------------------------------------------------- 缓存


def _cache_path(name: str) -> Path:
    return MCP_CACHE_DIR / f"{safe_name(name)}.json"


def read_cache(server: ServerConfig) -> list[dict]:
    """磁盘上的工具清单。指纹对不上就当没有。"""
    path = _cache_path(server.name)
    if not path.is_file():
        return []
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return []
    if data.get("fingerprint") != server.fingerprint():
        return []
    tools = data.get("tools")
    return [t for t in tools if isinstance(t, dict)] if isinstance(tools, list) else []


def write_cache(server: ServerConfig, tools: list[dict]) -> None:
    try:
        MCP_CACHE_DIR.mkdir(parents=True, exist_ok=True)
        _cache_path(server.name).write_text(
            json.dumps(
                {"fingerprint": server.fingerprint(), "tools": tools},
                ensure_ascii=False,
            )
            + "\n",
            encoding="utf-8",
        )
    except OSError:
        pass


# ---------------------------------------------------------------- 结果渲染


def render_result(result, limit: int = MAX_RESULT_CHARS) -> str:
    """MCP 的 content 块 → 一段文本。

    图片和二进制绝不进上下文：base64 一张图就是几万 token，
    而且现在也没有视觉模型能接。只留元信息。
    """
    if not isinstance(result, dict):
        return str(result or "")[:limit]

    blocks = result.get("content")
    is_error = bool(result.get("isError"))
    parts: list[str] = []

    if isinstance(blocks, list):
        for block in blocks:
            if not isinstance(block, dict):
                continue
            kind = block.get("type")
            if kind == "text":
                parts.append(str(block.get("text") or ""))
            elif kind in ("image", "audio"):
                mime = block.get("mimeType") or kind
                size = len(str(block.get("data") or ""))
                parts.append(f"[{kind} {mime}，约 {size // 1024} KB 已省略]")
            elif kind == "resource":
                res = block.get("resource") or {}
                parts.append(f"[资源 {res.get('uri') or '?'}]")
            else:
                parts.append(f"[{kind} 内容已省略]")

    if not parts:
        text = result.get("structuredContent")
        parts.append(json.dumps(text, ensure_ascii=False) if text else "（无内容）")

    body = "\n".join(p for p in parts if p).strip() or "（无内容）"
    if len(body) > limit:
        body = body[: int(limit * 0.7)] + f"\n…（已截断，原 {len(body)} 字符）"
    return f"{ERR}{body}" if is_error else body


# ---------------------------------------------------------------- 管理器


class MCPManager:
    def __init__(self) -> None:
        self._clients: dict[str, StdioClient] = {}
        self._config: list[ServerConfig] = []
        self._errors: dict[str, str] = {}
        self._injected: list[dict] = []
        self._dropped: list[dict] = []
        self._configured = False

    # ---------------------------------------------------------- 配置

    def servers(self) -> list[ServerConfig]:
        if not self._configured:
            self.reload()
        return list(self._config)

    def reload(self) -> None:
        """重读配置并重算注入集合。不启动任何进程。"""
        self._config = load_config()
        self._configured = True
        self.rebuild()

    def rebuild(self) -> None:
        """按缓存重算「注入哪些 schema、降级哪些」。"""
        budget = mcp_tool_budget()
        candidates: list[dict] = []
        for server in self._config:
            if not server.enabled:
                continue
            for raw in read_cache(server):
                name = str(raw.get("name") or "")
                if not name:
                    continue
                if server.tools and name not in server.tools:
                    continue  # 白名单之外的不注入
                candidates.append(
                    to_openai_tool(
                        tool_name(server.name, name),
                        str(raw.get("description") or ""),
                        raw.get("inputSchema") or raw.get("input_schema"),
                    )
                )
        injected, dropped = apply_budget(candidates, budget)

        # 降级摘要本身也吃 token，得从预算里扣掉再算一遍。
        # 不扣的话「1500 的预算」实际会花到 1700+。
        if dropped and budget > 0:
            reserve = estimate_text(self._summary_for(dropped))
            injected, dropped = apply_budget(candidates, max(0, budget - reserve))

        self._injected, self._dropped = injected, dropped

    # ---------------------------------------------------------- 给工具层用

    def tools(self) -> tuple[list[dict], dict]:
        """返回 (schemas, handlers)。和插件层同一个签名，方便合并。"""
        if not self._configured:
            self.reload()

        handlers: dict = {}
        for schema in self._injected:
            name = schema["function"]["name"]
            handlers[name] = self._make_handler(name)

        # 有降级的工具才注册元工具，否则白占 token
        if self._dropped:
            from .meta import META_SCHEMA

            handlers[META_SCHEMA["function"]["name"]] = self.call_by_name
            return [*self._injected, META_SCHEMA], handlers

        return list(self._injected), handlers

    def _make_handler(self, qualified: str):
        def handler(**kwargs) -> str:
            server, _, tool = qualified[len("mcp__") :].partition("__")
            return self.call(server, tool, kwargs)

        return handler

    def call(self, server: str, tool: str, arguments: dict | None = None) -> str:
        """真正发 tools/call。任何失败都转成 [ERR] 文本，永不抛。"""
        config = next((s for s in self.servers() if s.name == server), None)
        if config is None:
            return f"{ERR}没有名为 {server} 的 MCP server"
        if not config.enabled:
            return f"{ERR}{server} 已停用"

        try:
            client = self._ensure_client(config)
            result = client.request(
                "tools/call",
                {"name": tool, "arguments": arguments or {}},
                timeout=mcp_timeout() * 3,  # 工具执行通常比握手慢
            )
            return render_result(result, config.max_result)
        except MCPError as exc:
            return f"{ERR}{exc}"
        except Exception as exc:  # noqa: BLE001 兜住一切，别让循环崩
            return f"{ERR}调用 {server}.{tool} 失败：{type(exc).__name__}: {exc}"

    def call_by_name(self, server: str = "", tool: str = "", args: str = "{}") -> str:
        """`mcp_call` 元工具的入口：参数走 JSON 字符串。"""
        if not server or not tool:
            return f"{ERR}mcp_call 需要 server 和 tool 两个参数"
        try:
            parsed = json.loads(args or "{}", strict=False)
        except json.JSONDecodeError as exc:
            return f"{ERR}args 不是合法 JSON（{exc}）：{args[:200]}"
        if not isinstance(parsed, dict):
            return f"{ERR}args 必须是 JSON 对象"
        return self.call(server, tool, parsed)

    # ---------------------------------------------------------- 连接管理

    def _ensure_client(self, config: ServerConfig) -> StdioClient:
        client = self._clients.get(config.name)
        if client is not None and client.alive:
            return client
        if client is not None:
            client.close()

        client = StdioClient(
            config.command,
            config.args,
            name=config.name,
            env=config.env or None,
            cwd=config.cwd or None,
            timeout=mcp_timeout(),
        )
        client.start()
        self._clients[config.name] = client
        self._errors.pop(config.name, None)
        return client

    def sync(self, name: str) -> str:
        """连接一次、拉工具清单、写缓存。界面上的「同步」按钮走这里。"""
        config = next((s for s in self.servers() if s.name == name), None)
        if config is None:
            return f"没有名为 {name} 的 server"
        try:
            client = self._ensure_client(config)
            result = client.request("tools/list", {}, timeout=mcp_timeout())
        except MCPError as exc:
            self._errors[name] = str(exc)
            return str(exc)

        tools = (result or {}).get("tools") if isinstance(result, dict) else None
        tools = [t for t in tools if isinstance(t, dict) and t.get("name")] if tools else []
        write_cache(config, tools)
        self._errors.pop(name, None)
        self.rebuild()
        return f"{name}：{len(tools)} 个工具"

    def status(self) -> list[dict]:
        """给界面用：每个 server 的状态、工具数、token 占用。"""
        self.servers()
        rows: list[dict] = []
        injected_by_server: dict[str, list[dict]] = {}
        for tool in self._injected + self._dropped:
            name = tool["function"]["name"]
            if not name.startswith("mcp__"):
                continue
            server = name[len("mcp__") :].split("__", 1)[0]
            injected_by_server.setdefault(server, []).append(tool)

        for config in self._config:
            tools = read_cache(config)
            picked = injected_by_server.get(safe_name(config.name), [])
            rows.append(
                {
                    "name": config.name,
                    "command": " ".join([config.command, *config.args]),
                    "enabled": config.enabled,
                    "connected": bool(self._clients.get(config.name) and self._clients[config.name].alive),
                    "tools": len(tools),
                    "whitelist": len(config.tools),
                    "tokens": sum(tool_tokens(t) for t in picked),
                    "error": self._errors.get(config.name, ""),
                }
            )
        return rows

    def summary_text(self) -> str:
        """降级工具的一行签名清单，拼进系统提示词。

        一个工具十来个 token，比塞完整 schema 便宜一个数量级。
        """
        self.servers()  # 没 reload 过就顺手 reload
        return self._summary_for(self._dropped)

    @staticmethod
    def _summary_for(dropped: list[dict], limit: int = 40) -> str:
        if not dropped:
            return ""
        lines = [signature_line(tool) for tool in dropped]
        head = (
            f"还有 {len(lines)} 个 MCP 工具没展开（省 token）。要调它们就用 "
            "mcp_call(server, tool, args)，server/tool 取下面前缀："
        )
        body = "\n".join(f"- {line}" for line in lines[:40])
        if len(lines) > 40:
            body += f"\n- …另有 {len(lines) - 40} 个"
        return head + "\n" + body

    # ---------------------------------------------------------- 收尾

    def close_all(self) -> None:
        for client in self._clients.values():
            client.close()
        self._clients.clear()

    @property
    def budget(self) -> int:
        return mcp_tool_budget()

    def usage(self) -> dict:
        """当前 MCP 工具占了多少 token，界面上要能看到。"""
        return {
            "injected": len(self._injected),
            "dropped": len(self._dropped),
            "tokens": sum(tool_tokens(t) for t in self._injected),
            "budget": mcp_tool_budget(),
            "summary_tokens": estimate_text(self.summary_text()),
        }


mcp_manager = MCPManager()
