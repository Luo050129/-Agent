"""内核闭环测试：边界拦截、自评返工、卡死检测、结果压缩、状态观测。

用脚本化假模型，不碰网络。`python tests/test_kernel.py` 直接跑。
"""

from __future__ import annotations

import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from miniagent.core import Agent, Session, Sink  # noqa: E402
from miniagent.core.completion import VERIFY_PROMPT  # noqa: E402
from miniagent.core.context import RecordLog  # noqa: E402


def tool_call(name: str, args: dict, call_id: str = "call_1") -> dict:
    return {"id": call_id, "type": "function", "function": {"name": name, "arguments": json.dumps(args)}}


class ScriptLLM:
    """按脚本回放；最后一条用户消息是验证探针时走探针脚本。"""

    def __init__(
        self,
        replies: list[tuple[str, list[dict]]],
        probe: str = "DONE",
        probe_calls: list[dict] | None = None,
    ) -> None:
        self.replies = list(replies)
        self.probe = probe
        self.probe_calls = probe_calls or []
        self.turns = 0
        self.probes = 0

    def stream(self, messages, tools=None, on_text=None):
        if messages and messages[-1].get("content") == VERIFY_PROMPT:  # 验证探针
            self.probes += 1
            return self.probe, self.probe_calls
        self.turns += 1
        if not self.replies:
            return "结束", []
        text, calls = self.replies.pop(0)
        if on_text and text:
            on_text(text)
        return text, calls


class CaptureSink(Sink):
    def __init__(self) -> None:
        self.states: list[dict] = []
        self.errors: list[str] = []
        self.results: list[str] = []
        self.texts: list[str] = []

    def text(self, piece: str) -> None:
        self.texts.append(piece)

    def tool_result(self, result: str) -> None:
        self.results.append(result)

    def state(self, snapshot: dict) -> None:
        self.states.append(snapshot)

    def error(self, message: str) -> None:
        self.errors.append(message)


def _run(llm, max_steps: int = 8, self_check: bool = False):
    session = Session()
    session.add_user("做点事")
    sink = CaptureSink()
    agent = Agent(llm, max_steps=max_steps, self_check=self_check, records=RecordLog(enabled=False))
    answer = agent.run(session, sink=sink, safe=False)
    return session, sink, answer


def test_rules_block() -> list[tuple[str, bool]]:
    llm = ScriptLLM(
        [
            ("", [tool_call("write_file", {"path": "C:/Windows/evil.txt", "content": "x"})]),
            ("被拒了，换个做法", []),
        ]
    )
    session, sink, answer = _run(llm)
    tool_msgs = [m["content"] for m in session.messages if m["role"] == "tool"]
    return [
        ("越界写入被拦下", bool(tool_msgs) and "被规则拒绝" in tool_msgs[-1]),
        ("拦截原因回灌给模型", "写入范围" in tool_msgs[-1]),
        ("循环继续并收尾", "换个做法" in answer),
    ]


def test_self_check_rework() -> list[tuple[str, bool]]:
    llm = ScriptLLM(
        [
            ("", [tool_call("list_dir", {"path": "."})]),
            ("差不多好了", []),
            ("结论：目录里有这些文件", []),
        ],
        probe="TODO: 还缺一句结论",
    )
    session, sink, answer = _run(llm, self_check=True)
    nudges = [m["content"] for m in session.messages if m["role"] == "user" and "继续完成" in str(m["content"])]
    return [
        ("触发了一次自评", llm.probes == 1),
        ("未完成时插入返工提示", bool(nudges) and "结论" in nudges[0]),
        ("返工后拿到最终答案", "结论" in answer),
        ("自评只做一次就收", llm.probes == 1),
    ]


def test_verify_pass() -> list[tuple[str, bool]]:
    """模型给出验证工具调用 → 内核真执行 → 结果健康 → 带着 verified 交付。"""
    llm = ScriptLLM(
        [
            ("", [tool_call("write_file", {"path": ".agent/_verify_probe.txt", "content": "探针"}, "c1")]),
            ("写好了", []),
        ],
        probe_calls=[tool_call("read_file", {"path": ".agent/_verify_probe.txt"}, "v1")],
    )
    session, sink, answer = _run(llm, self_check=True)
    last = sink.states[-1] if sink.states else {}
    tool_msgs = [m["content"] for m in session.messages if m["role"] == "tool"]
    return [
        ("验证调用真被执行", "探针" in tool_msgs[-1]),
        ("验证通过后交付", "写好了" in answer),
        ("状态标记已验证", last.get("verified") is True),
        ("验证消息进了历史", any(c.get("id") == "v1" for m in session.messages for c in (m.get("tool_calls") or []))),
    ]


def test_verify_fail() -> list[tuple[str, bool]]:
    """验证调用失败 → 打回返工，不能带着假完成交付。"""
    llm = ScriptLLM(
        [
            ("", [tool_call("list_dir", {"path": "."}, "c1")]),
            ("应该好了", []),
            ("这次真的好了", []),
        ],
        probe_calls=[tool_call("read_file", {"path": "这个文件不存在_验证用.txt"}, "v1")],
    )
    session, sink, answer = _run(llm, self_check=True)
    last = sink.states[-1] if sink.states else {}
    rework = [str(m["content"]) for m in session.messages if m["role"] == "user" and "[验证未通过]" in str(m["content"])]
    return [
        ("验证失败触发返工", bool(rework) and "不存在" in rework[0]),
        ("打回后重新交付", "这次真的好了" in answer),
        ("未验证不算数", last.get("verified") is False),
        ("探针只消耗一次预算", llm.probes == 1),
    ]


def test_stuck() -> list[tuple[str, bool]]:
    same = tool_call("list_dir", {"path": "."})
    llm = ScriptLLM([("", [same]), ("", [same]), ("", [same]), ("", [same])])
    session, sink, answer = _run(llm)
    blocked = [s for s in sink.states if s["status"] == "blocked"]
    return [
        ("重复调用被识别", bool(blocked)),
        ("给出了停止原因", any("重复" in e for e in sink.errors)),
        ("没有无限空转", llm.turns <= 3),
    ]


def test_compaction() -> list[tuple[str, bool]]:
    llm = ScriptLLM(
        [
            ("", [tool_call("run_python", {"code": "print('x' * 6000)"})]),
            ("压完了", []),
        ]
    )
    session, sink, answer = _run(llm)
    tool_msgs = [m["content"] for m in session.messages if m["role"] == "tool"]
    return [
        ("超长结果被压缩", bool(tool_msgs) and "已压缩" in tool_msgs[-1]),
        ("压缩后长度可控", bool(tool_msgs) and len(tool_msgs[-1]) < 3500),
    ]


def test_state_events() -> list[tuple[str, bool]]:
    llm = ScriptLLM(
        [
            ("", [tool_call("list_dir", {"path": "."})]),
            ("好了", []),
        ]
    )
    session, sink, answer = _run(llm, self_check=True)
    statuses = [s["status"] for s in sink.states]
    last = sink.states[-1] if sink.states else {}
    return [
        ("有规划阶段", "planning" in statuses),
        ("有执行阶段", "tooling" in statuses),
        ("有完成状态", "done" in statuses),
        ("记录了上下文占用", last.get("context_tokens", 0) > 0),
        ("记录了输出 token", last.get("output_tokens", 0) > 0),
        ("完成度到 1", last.get("completion") == 1.0),
    ]


def test_max_steps() -> list[tuple[str, bool]]:
    llm = ScriptLLM(
        [
            ("", [tool_call("list_dir", {"path": "."}, "c1")]),
            ("", [tool_call("list_dir", {"path": "tests"}, "c2")]),
            ("", [tool_call("list_dir", {"path": "miniagent"}, "c3")]),
        ]
    )
    session, sink, answer = _run(llm, max_steps=2)
    blocked = [s for s in sink.states if s["status"] == "blocked"]
    return [
        ("撞到步数上限会标 blocked", bool(blocked)),
        ("给出收尾提示", any("最大步数" in e for e in sink.errors)),
    ]


def main() -> int:
    print("[kernel] 开始")
    checks = (
        test_rules_block()
        + test_self_check_rework()
        + test_verify_pass()
        + test_verify_fail()
        + test_stuck()
        + test_compaction()
        + test_state_events()
        + test_max_steps()
    )
    for label, ok in checks:
        print(f"  {'PASS' if ok else 'FAIL'}  {label}")
    ok_all = all(ok for _, ok in checks)
    print(f"[kernel] {'全部通过' if ok_all else '存在失败项'}")
    return 0 if ok_all else 1


if __name__ == "__main__":
    raise SystemExit(main())
