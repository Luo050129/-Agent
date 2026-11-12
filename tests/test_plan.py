"""两种工作模式测试：ReAct 与 Plan-and-Execute，以及模式切换。

`python tests/test_plan.py` 直接跑，不碰网络。
"""

from __future__ import annotations

import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from miniagent.core import Agent, Session, Sink  # noqa: E402
from miniagent.core.context import RecordLog  # noqa: E402
from miniagent.core.loop import normalize_mode, parse_plan  # noqa: E402

PLAN_TWO = json.dumps(
    [
        {"goal": "看一下目录里有什么", "done_when": "拿到目录列表"},
        {"goal": "总结目录结构", "done_when": "给出结论"},
    ],
    ensure_ascii=False,
)
PLAN_ONE = json.dumps([{"goal": "读 README", "done_when": "读到内容"}], ensure_ascii=False)


def call(name: str, args: dict, cid: str = "c1") -> dict:
    return {"id": cid, "type": "function", "function": {"name": name, "arguments": json.dumps(args)}}


class PlanLLM:
    """按「规划 / 执行 / 汇总」三类调用分别回放。"""

    model = "fake-plan"
    last_attempts = 1

    def __init__(self, plan: str, replies: list[tuple[str, list]], synth: str = "最终结论：都搞定了") -> None:
        self.plan = plan
        self.replies = list(replies)
        self.synth = synth
        self.planner_calls = 0
        self.exec_calls = 0
        self.synth_calls = 0
        self.plans_seen: list[str] = []

    def stream(self, messages, tools=None, on_text=None):
        last = str(messages[-1].get("content") or "")

        if tools is None and ("拆成可执行" in last or "重新规划" in last):
            self.planner_calls += 1
            self.plans_seen.append(last[:40])
            if self.planner_calls == 1:
                return self.plan, []
            return PLAN_ONE, []

        if tools is None and "最终答复" in last:
            self.synth_calls += 1
            if on_text:
                on_text(self.synth)
            return self.synth, []

        self.exec_calls += 1
        if not self.replies:
            return "这一步完成了", []
        text, calls = self.replies.pop(0)
        if on_text and text:
            on_text(text)
        return text, calls


class CaptureSink(Sink):
    def __init__(self) -> None:
        self.plans: list[tuple[list, bool]] = []
        self.steps: list[tuple[int, str, str]] = []
        self.notices: list[str] = []
        self.errors: list[str] = []
        self.texts: list[str] = []

    def plan(self, steps, replanned=False):
        self.plans.append((steps, replanned))

    def plan_step(self, index, status, note=""):
        self.steps.append((index, status, note))

    def notice(self, message):
        self.notices.append(message)

    def error(self, message):
        self.errors.append(message)

    def text(self, piece):
        self.texts.append(piece)


def run_agent(llm, mode: str = "plan", max_steps: int = 12):
    session = Session()
    session.add_user("看看项目结构再总结一下")
    sink = CaptureSink()
    agent = Agent(
        llm,
        max_steps=max_steps,
        self_check=False,
        records=RecordLog(enabled=False),
        mode=mode,
    )
    answer = agent.run(session, sink=sink)
    return session, sink, answer


# ---------------------------------------------------------------- 单元


def test_parse_and_mode() -> list[tuple[str, bool]]:
    plain = parse_plan(PLAN_TWO)
    fenced = parse_plan("```json\n" + PLAN_TWO + "\n```")
    strings = parse_plan('["第一步", "第二步"]')
    broken = parse_plan("我觉得应该先看看目录")
    return [
        ("标准 JSON 能解析", len(plain) == 2 and plain[0]["goal"] == "看一下目录里有什么"),
        ("带代码块也能解析", len(fenced) == 2),
        ("字符串数组也能解析", len(strings) == 2 and strings[0]["done_when"] == ""),
        ("非 JSON 返回空", broken == []),
        ("模式名归一化", normalize_mode("PLAN") == "plan"),
        ("非法模式回落到 react", normalize_mode("随便写的") == "react"),
        ("默认模式是 react", normalize_mode(None) == "react"),
    ]


# ---------------------------------------------------------------- 闭环


def test_plan_happy_path() -> list[tuple[str, bool]]:
    llm = PlanLLM(
        PLAN_TWO,
        replies=[
            ("先看目录", [call("list_dir", {"path": "."}, "a1")]),  # 第 1 步：调工具
            ("目录里有 skills/tests/miniagent", []),  # 第 1 步：给结果
            ("结构是五层包", []),  # 第 2 步：直接给结果
        ],
    )
    session, sink, answer = run_agent(llm)

    return [
        ("先规划了一次", llm.planner_calls == 1),
        ("广播了计划", len(sink.plans) == 1 and len(sink.plans[0][0]) == 2),
        ("两步都执行了", llm.exec_calls == 3),
        ("步骤状态有 running", any(s[1] == "running" for s in sink.steps)),
        ("两步都标 done", [s[1] for s in sink.steps if s[1] != "running"] == ["done", "done"]),
        ("汇总了一次", llm.synth_calls == 1),
        ("最终答复是汇总结果", answer == "最终结论：都搞定了"),
        ("对话里留下了步骤指令", any("[计划 第 1/2 步]" in str(m.get("content")) for m in session.messages)),
        ("工具结果进了历史", any(m["role"] == "tool" for m in session.messages)),
    ]


def test_plan_fallback() -> list[tuple[str, bool]]:
    llm = PlanLLM("我不太确定该怎么拆", replies=[("直接回答了", [])])
    session, sink, answer = run_agent(llm)
    return [
        ("计划解析失败会提示", any("计划没解析出来" in n for n in sink.notices)),
        ("回退到 ReAct 仍给答复", bool(answer)),
        ("规划只试了一次", llm.planner_calls == 1),
    ]


def test_replan_on_failure() -> list[tuple[str, bool]]:
    """第一步越界被拦 → 触发重规划，换成一步可做的小任务。"""
    llm = PlanLLM(
        PLAN_TWO,
        replies=[
            ("", [call("write_file", {"path": "C:/Windows/x.txt", "content": "x"}, "b1")]),  # 第 1 步失败
            ("读完 README 了", []),  # 重规划后的那一步
        ],
    )
    session, sink, answer = run_agent(llm)
    replanned = [p for p in sink.plans if p[1]]
    return [
        ("失败触发了重规划", llm.planner_calls == 2),
        ("广播了新计划", len(replanned) == 1 and len(replanned[0][0]) == 1),
        ("有步骤被标 failed", any(s[1] == "failed" for s in sink.steps)),
        ("重规划后能收尾", bool(answer) and "最终结论" in answer),
    ]


def test_replan_limit() -> list[tuple[str, bool]]:
    """重规划后仍失败 → 不再无限试，交出报告。"""
    bad = ("", [call("write_file", {"path": "C:/Windows/y.txt", "content": "y"}, "c1")])
    llm = PlanLLM(PLAN_TWO, replies=[bad, bad, bad, bad])
    session, sink, answer = run_agent(llm)
    return [
        ("重规划只做一次", llm.planner_calls == 2),
        ("最终给出停止说明", "没跑完" in answer or "没做成" in answer),
        ("状态标 blocked", any("停止" in e for e in sink.errors) or "没做成" in answer),
    ]


def test_react_mode_unchanged() -> list[tuple[str, bool]]:
    llm = PlanLLM(PLAN_TWO, replies=[("", [call("list_dir", {"path": "."}, "r1")]), ("看完了", [])])
    session, sink, answer = run_agent(llm, mode="react")
    return [
        ("react 模式不规划", llm.planner_calls == 0),
        ("react 模式直接给答案", answer == "看完了"),
        ("react 模式不广播计划", sink.plans == []),
    ]


def main() -> int:
    print("[plan] 开始")
    checks = (
        test_parse_and_mode()
        + test_plan_happy_path()
        + test_plan_fallback()
        + test_replan_on_failure()
        + test_replan_limit()
        + test_react_mode_unchanged()
    )
    for label, ok in checks:
        print(f"  {'PASS' if ok else 'FAIL'}  {label}")
    ok_all = all(ok for _, ok in checks)
    print(f"[plan] {'全部通过' if ok_all else '存在失败项'}")
    return 0 if ok_all else 1


if __name__ == "__main__":
    raise SystemExit(main())
