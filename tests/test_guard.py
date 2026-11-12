"""熔断测试：无限循环、反复失败、预算超限、被边界连拒时都要能自己收手。

`python tests/test_guard.py` 直接跑，不碰网络。
"""

from __future__ import annotations

import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from miniagent.core import Agent, Session, Sink  # noqa: E402
from miniagent.core.context import RecordLog  # noqa: E402
from miniagent.core.guard import Guard, Limits  # noqa: E402


def call(name: str, args: dict, cid: str = "c1") -> dict:
    return {"id": cid, "type": "function", "function": {"name": name, "arguments": json.dumps(args)}}


class ScriptLLM:
    """按脚本回放；tools=None 视为出口自评。"""

    model = "fake-guard"
    last_attempts = 1

    def __init__(self, replies, probe: str = "DONE") -> None:
        self.replies = list(replies)
        self.probe = probe
        self.turns = 0

    def stream(self, messages, tools=None, on_text=None):
        if tools is None:
            return self.probe, []
        self.turns += 1
        if not self.replies:
            return "结束", []
        text, calls = self.replies.pop(0)
        if on_text and text:
            on_text(text)
        return text, calls


class CaptureSink(Sink):
    def __init__(self) -> None:
        self.texts: list[str] = []
        self.errors: list[str] = []
        self.notices: list[str] = []
        self.states: list[dict] = []

    def text(self, piece: str) -> None:
        self.texts.append(piece)

    def state(self, snapshot: dict) -> None:
        self.states.append(snapshot)

    def notice(self, message: str) -> None:
        self.notices.append(message)

    def error(self, message: str) -> None:
        self.errors.append(message)


def run_loop(llm, limits: Limits | None = None, max_steps: int = 8):
    session = Session()
    session.add_user("干活")
    sink = CaptureSink()
    agent = Agent(
        llm,
        max_steps=max_steps,
        limits=limits or Limits(),
        self_check=False,
        records=RecordLog(enabled=False),
    )
    answer = agent.run(session, sink=sink)
    return session, sink, answer


# ---------------------------------------------------------------- 单元


def test_guard_unit() -> list[tuple[str, bool]]:
    guard = Guard(Limits(max_same_call=2, max_failures_per_call=2))
    checks = [
        ("第一次放行", guard.before_tool("list_dir", '{"path":"."}') is None),
    ]
    guard.after_tool("list_dir", '{"path":"."}', "正常结果")
    checks.append(("第二次放行", guard.before_tool("list_dir", '{"path":"."}') is None))
    guard.after_tool("list_dir", '{"path":"."}', "正常结果")
    checks.append(("第三次拦截", guard.before_tool("list_dir", '{"path":"."}') is not None))

    guard2 = Guard(Limits(max_failures_per_call=2))
    guard2.after_tool("read_file", '{"path":"a"}', "读取失败：没有这个文件")
    guard2.after_tool("read_file", '{"path":"a"}', "读取失败：没有这个文件")
    checks.append(("反复失败会被拦", guard2.before_tool("read_file", '{"path":"a"}') is not None))

    guard3 = Guard(Limits(max_tool_calls=1))
    guard3.after_tool("list_dir", '{"path":"."}', "ok")
    checks.append(("工具总数上限生效", guard3.before_tool("list_dir", '{"path":"x"}') is not None))

    guard4 = Guard(Limits(max_seconds=5))
    guard4.clock = lambda: guard4.start + 10  # 时间穿越
    checks.append(("时间预算生效", guard4.check_stop() is not None))

    guard5 = Guard(Limits(max_denials=2))
    guard5.after_tool("write_file", '{"path":"C:/x"}', "被规则拒绝：写入范围之外", denied=True)
    guard5.after_tool("write_file", '{"path":"C:/y"}', "被规则拒绝：写入范围之外", denied=True)
    checks.append(("连续越界会停", guard5.check_stop() is not None))

    guard6 = Guard(Limits(max_unproductive=3))
    for _ in range(4):
        guard6.after_tool("list_dir", '{"path":"."}', "一模一样的输出")
    checks.append(("无进展会停", guard6.check_stop() is not None))
    checks.append(("报告不是空的", "工具调用" in guard6.report("测试")))
    return checks


# ---------------------------------------------------------------- 闭环


def test_infinite_loop() -> list[tuple[str, bool]]:
    same = call("list_dir", {"path": "."})
    llm = ScriptLLM([("", [same]), ("", [same]), ("", [same]), ("", [same]), ("", [same])])
    session, sink, answer = run_loop(llm)
    return [
        ("重复调用被熔断", any("重复" in e for e in sink.errors)),
        ("没有无限空转", llm.turns <= 3),
        ("给了收手报告", "没跑完" in answer),
        ("报告里有卡点说明", "工具调用" in answer),
        ("状态标为 blocked", any(s["status"] == "blocked" for s in sink.states)),
    ]


def test_repeated_failure() -> list[tuple[str, bool]]:
    missing = call("read_file", {"path": "不存在的文件.txt"})
    llm = ScriptLLM([("", [missing])] * 5)
    session, sink, answer = run_loop(llm)
    return [
        ("反复失败被拦", any("失败" in e or "熔断" in e for e in sink.errors)),
        ("没有一直重试", llm.turns <= 4),
        ("报告里提到失败", "失败" in answer),
    ]


def test_tool_budget() -> list[tuple[str, bool]]:
    calls = [call("list_dir", {"path": p}, f"c{i}") for i, p in enumerate([".", "tests", "skills", "docs"])]
    llm = ScriptLLM([("", [c]) for c in calls] + [("", [call("list_dir", {"path": "extra"}, "cx")])])
    session, sink, answer = run_loop(llm, limits=Limits(max_tool_calls=2))
    return [
        ("超出工具预算会停", any("上限" in e or "熔断" in e for e in sink.errors)),
        ("没有执行剩余调用", session.tool_calls <= 3),
    ]


def test_time_budget() -> list[tuple[str, bool]]:
    llm = ScriptLLM([("", [call("list_dir", {"path": "."})])] * 5)
    session = Session()
    session.add_user("干活")
    sink = CaptureSink()
    agent = Agent(llm, max_steps=8, self_check=False, records=RecordLog(enabled=False))
    original = Guard.check_stop

    def fake_check(self):
        return "超出时间预算（测试）"

    Guard.check_stop = fake_check
    try:
        answer = agent.run(session, sink=sink)
    finally:
        Guard.check_stop = original
    return [
        ("时间超限会停", any("时间预算" in e for e in sink.errors)),
        ("时间超限给报告", "超出时间预算" in answer),
    ]


def test_denial_streak() -> list[tuple[str, bool]]:
    bad = [call("write_file", {"path": "C:/Windows/x.txt", "content": f"x{i}"}, f"d{i}") for i in range(4)]
    llm = ScriptLLM([("", [c]) for c in bad])
    session, sink, answer = run_loop(llm)
    return [
        ("越界被规则拦", any("写入范围" in str(m.get("content")) for m in session.messages if m["role"] == "tool")),
        ("连续越界会收手", any("边界" in e or "拒绝" in e for e in sink.errors)),
        ("没有真的写到系统盘", not os.path.exists("C:/Windows/x.txt")),
    ]


def test_failure_classification() -> list[tuple[str, bool]]:
    """失败判定：前缀优先，关键词只做兜底，且只看开头。"""
    from miniagent.core.guard import ERR_PREFIX, OK_PREFIX, classify, is_failure
    from miniagent.tools import execute
    from miniagent.tools._util import ERR, OK, tag_result

    checks = [
        ("前缀常量两边一致", ERR.strip() == ERR_PREFIX and OK.strip() == OK_PREFIX),
        ("[ERR] 判为 error", classify("[ERR] 读不到") == "error" and is_failure("[ERR] 读不到")),
        ("[OK] 判为 ok", classify("[OK] 完成了") == "ok" and not is_failure("[OK] 完成了")),
        ("无前缀判为 unknown", classify("正常结果") == "unknown"),
        # 关键改进：成功内容含歧义词，加了豁免就不会被误判
        ("含敏感词的正常内容不误判", not is_failure("[OK] 这份文档讲的是磁盘故障与数据不存在时的恢复")),
        ("同样的内容不加豁免会被兜底判失败",
         is_failure("这份文档讲的是磁盘故障与数据不存在时的恢复")),
        # 敏感词出现在正文深处、不在开头，不该判失败
        ("正文深处的敏感词不误判",
         not is_failure("目录共 3 项：\n" + "说明" * 120 + "\n其中一项叫失败重试")),
        ("tag_result 给歧义成功结果补 OK",
         tag_result("任务失败率统计：12%").startswith(OK_PREFIX)),
        ("tag_result 不碰已有的 ERR", tag_result("[ERR] 失败了") == "[ERR] 失败了"),
        ("读不存在的文件带 ERR", execute("read_file", '{"path": "C:/nope_xyz_1.txt"}').startswith(ERR_PREFIX)),
        ("跑失败的命令带 ERR", execute("run_shell", '{"command": "exit 7"}').startswith(ERR_PREFIX)),
        ("未知工具带 ERR", execute("no_such_tool", "{}").startswith(ERR_PREFIX)),
        ("参数不是 JSON 带 ERR", execute("read_file", "not json").startswith(ERR_PREFIX)),
        ("成功命令不带 ERR", not is_failure(execute("run_shell", '{"command": "echo ok"}'))),
    ]
    return checks


def main() -> int:
    print("[guard] 开始")
    checks = (
        test_guard_unit()
        + test_failure_classification()
        + test_infinite_loop()
        + test_repeated_failure()
        + test_tool_budget()
        + test_time_budget()
        + test_denial_streak()
    )
    for label, ok in checks:
        print(f"  {'PASS' if ok else 'FAIL'}  {label}")
    ok_all = all(ok for _, ok in checks)
    print(f"[guard] {'全部通过' if ok_all else '存在失败项'}")
    return 0 if ok_all else 1


if __name__ == "__main__":
    raise SystemExit(main())
