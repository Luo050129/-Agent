"""上下文管理层测试：token 估算、裁剪、摘要、记录。

不需要网络，`python tests/test_context.py` 直接跑。
"""

from __future__ import annotations

import json
import os
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from miniagent.core.context import ContextManager, RecordLog, estimate_messages, estimate_text  # noqa: E402


def _long_history(rounds: int = 20) -> list[dict]:
    messages = [{"role": "system", "content": "系统提示"}]
    for i in range(rounds):
        messages.append({"role": "user", "content": f"第 {i} 个问题" + "补充说明" * 30})
        messages.append(
            {
                "role": "assistant",
                "content": "",
                "tool_calls": [
                    {
                        "id": f"call_{i}",
                        "type": "function",
                        "function": {"name": "list_dir", "arguments": json.dumps({"path": "."})},
                    }
                ],
            }
        )
        messages.append({"role": "tool", "tool_call_id": f"call_{i}", "content": "结果" * 200})
    return messages


def test_estimate() -> list[tuple[str, bool]]:
    checks = [
        ("空字符串为 0", estimate_text("") == 0),
        ("中文按字算", estimate_text("你好世界") == 4),
        ("英文按 4 字符 1 token", 1 <= estimate_text("hello world") <= 4),
        ("消息列表有开销", estimate_messages([{"role": "user", "content": "你好"}]) > 2),
    ]
    return checks


def test_compact() -> list[tuple[str, bool]]:
    ctx = ContextManager(tool_result_tokens=100)
    short = "短结果"
    long = "A" * 1000  # 1000 英文字符 ≈ 250 token
    compacted = ctx.compact_tool_result(long)
    before = estimate_text(long)
    after = estimate_text(compacted)
    return [
        ("短结果原样返回", ctx.compact_tool_result(short) == short),
        ("长结果被压缩", after < before),
        ("压缩后有提示标记", "已压缩" in compacted),
        ("压缩后仍在限额附近", after <= 140),
    ]


def test_prepare_small() -> list[tuple[str, bool]]:
    ctx = ContextManager(budget=32000)
    messages = [{"role": "system", "content": "系统"}, {"role": "user", "content": "你好"}]
    view, stats = ctx.prepare(messages)
    return [
        ("小历史不裁剪", stats.dropped == 0),
        ("消息条数不变", len(view) == len(messages)),
        ("统计里有 token 数", stats.tokens > 0),
        ("原始历史未被修改", len(messages) == 2),
    ]


def test_prepare_huge() -> list[tuple[str, bool]]:
    ctx = ContextManager(budget=600, keep_recent=4)
    messages = _long_history()
    view, stats = ctx.prepare(messages)
    checks = [
        ("大历史被裁剪", stats.dropped > 0),
        ("插入了摘要", stats.summarized),
        ("第一条仍是 system", view[0]["role"] == "system"),
        ("第二条是摘要", view[1]["role"] == "system" and "早期上下文摘要" in view[1]["content"]),
        ("裁剪后 token 明显变小", stats.tokens < estimate_messages(messages)),
        ("裁剪后不超预算", stats.tokens <= 600),
        ("至少留下一条历史", len(view) >= 2),
    ]

    # 不能出现孤儿 tool 消息
    parents: set[str] = set()
    orphan = False
    for msg in view:
        if msg["role"] == "assistant":
            for call in msg.get("tool_calls") or []:
                parents.add(call["id"])
        elif msg["role"] == "tool":
            if msg.get("tool_call_id") not in parents:
                orphan = True
    checks.append(("没有孤儿 tool 消息", not orphan))
    return checks


def test_records() -> list[tuple[str, bool]]:
    tmp = tempfile.mkdtemp(prefix="records_")
    log = RecordLog(directory=Path(tmp))
    log.write(step=1, tool="list_dir", args='{"path": "."}', result="ok" * 400)
    files = list(Path(tmp).glob("*.jsonl"))
    lines = files[0].read_text(encoding="utf-8").splitlines() if files else []

    off = RecordLog(directory=Path(tmp) / "off", enabled=False)
    off.write(step=1, tool="x", args="{}", result="y")

    return [
        ("记录文件已生成", len(files) == 1),
        ("写了一行 json", len(lines) == 1),
        ("字段齐全", all(k in json.loads(lines[0]) for k in ("ts", "step", "tool", "args", "result", "ok"))),
        ("结果被截断", len(json.loads(lines[0])["result"]) <= 500),
        ("关闭时不写文件", not (Path(tmp) / "off").exists()),
    ]


def test_budget_respected() -> list[tuple[str, bool]]:
    """小预算也要真的把上下文压下来，包括摘要本身的 token。"""
    checks = []
    for budget in (300, 600, 1200):
        ctx = ContextManager(budget=budget)
        view, stats = ctx.prepare(_long_history())
        checks.append((f"budget={budget} 不超预算", stats.tokens <= budget))
        checks.append((f"budget={budget} 有裁剪", stats.dropped > 0 and len(view) >= 2))
    return checks


def main() -> int:
    print("[context] 开始")
    checks = (
        test_estimate()
        + test_compact()
        + test_prepare_small()
        + test_prepare_huge()
        + test_budget_respected()
        + test_records()
    )
    for label, ok in checks:
        print(f"  {'PASS' if ok else 'FAIL'}  {label}")
    ok_all = all(ok for _, ok in checks)
    print(f"[context] {'全部通过' if ok_all else '存在失败项'}")
    return 0 if ok_all else 1


if __name__ == "__main__":
    raise SystemExit(main())
