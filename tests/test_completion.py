"""完成度判定测试：快判打分 + 验证探针解析。

`python tests/test_completion.py` 直接跑。
"""

from __future__ import annotations

import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from miniagent.core.completion import CompletionJudge, MAX_VERIFY_CALLS, VERIFY_PROMPT  # noqa: E402
from miniagent.core.state import TaskState  # noqa: E402


def _call(name: str = "read_file", call_id: str = "v1") -> dict:
    return {
        "id": call_id,
        "type": "function",
        "function": {"name": name, "arguments": json.dumps({"path": "x"})},
    }


class _Reply:
    def __init__(self, text: str, calls: list[dict] | None = None) -> None:
        self.text = text
        self.calls = calls or []

    def stream(self, messages, tools=None, on_text=None):
        assert tools is not None and messages[-1].get("content") == VERIFY_PROMPT
        return self.text, self.calls


class _Boom:
    def stream(self, messages, tools=None, on_text=None):
        raise RuntimeError("网络炸了")


def main() -> int:
    print("[completion] 开始")
    checks: list[tuple[str, bool]] = []
    judge = CompletionJudge(llm=None, enabled=False)

    state = TaskState()
    checks.append(("没动工具时完成度低", judge.heuristic(state, []) <= 0.5))

    state = TaskState(step=1, tool_calls=2)
    checks.append(("工具都成功则分数较高", judge.heuristic(state, ["ok", "done"]) >= 0.6))

    state = TaskState(step=3, tool_calls=6)
    checks.append(
        ("连续报错会被扣分", judge.heuristic(state, ["执行异常", "被规则拒绝", "文件不存在"]) <= 0.45)
    )
    checks.append(("分数被夹在 0.1~0.95", 0.1 <= judge.heuristic(TaskState(tool_calls=1), ["x"] * 10) <= 0.95))

    msgs = [{"role": "user", "content": "x"}]

    got = CompletionJudge(_Reply("DONE"), enabled=True).verify(msgs, tools=[{}])
    checks.append(("只说 DONE 无证据 → 放行但未验证", got == ("noverify", "")))

    kind, payload = CompletionJudge(_Reply("TODO: 还缺结论"), enabled=True).verify(msgs, tools=[{}])
    checks.append(("TODO 判为未完成", kind == "todo" and "结论" in payload))

    kind, payload = CompletionJudge(_Reply("NOVERIFY: 纯问答"), enabled=True).verify(msgs, tools=[{}])
    checks.append(("NOVERIFY 带原因放行", kind == "noverify" and payload == "纯问答"))

    kind, calls = CompletionJudge(_Reply("", [_call()]), enabled=True).verify(msgs, tools=[{}])
    checks.append(("验证调用原样返回", kind == "call" and len(calls) == 1 and calls[0]["id"] == "v1"))

    many = CompletionJudge(_Reply("", [_call(call_id=f"v{i}") for i in range(8)]), enabled=True)
    kind, calls = many.verify(msgs, tools=[{}])
    checks.append(("验证调用数被封顶", kind == "call" and len(calls) == MAX_VERIFY_CALLS))

    kind, payload = CompletionJudge(_Boom(), enabled=True).verify(msgs, tools=[{}])
    checks.append(("探针失败不阻塞交付", kind == "noverify" and payload == ""))

    kind, payload = CompletionJudge(_Reply("DONE"), enabled=False).verify(msgs, tools=[{}])
    checks.append(("关掉验证直接放行", kind == "noverify"))

    for label, ok in checks:
        print(f"  {'PASS' if ok else 'FAIL'}  {label}")
    ok_all = all(ok for _, ok in checks)
    print(f"[completion] {'全部通过' if ok_all else '存在失败项'}")
    return 0 if ok_all else 1


if __name__ == "__main__":
    raise SystemExit(main())
