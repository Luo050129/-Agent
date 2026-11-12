"""核心层：Agent 内核，两种工作模式。

    react（默认）  边做边看：问模型 → 要工具就执行 → 回灌 → 再问，直到不再要工具
    plan          先规划再执行：先出一份步骤清单，逐步执行、逐步回填，卡住就重规划

两种模式共用同一套基础设施：上下文裁剪、边界、熔断、状态观测、工具执行、完成度判定。
差别只在「谁决定下一步」—— 模型每轮临场决定，还是先定一份计划再照着做。

模式只是策略，加新模式写一个 _run_xxx 循环即可，别动公共部分。
"""

from __future__ import annotations

import json
import threading

from ..config import default_mode
from ..model import LLMError
from ..tools import DANGEROUS, TOOL_SCHEMAS, execute, sandbox
from . import safety
from .completion import CompletionJudge
from .context import ContextManager, RecordLog, estimate_text
from .events import Sink
from .guard import Guard, Limits, is_failure
from .rules import Policy, risk_hint
from .session import Session
from .state import TaskState

PLAN_INSTRUCTION = """先别动手。把用户这个任务拆成可执行的步骤，只输出一个 JSON 数组，不要任何解释和前后缀。

格式：
[{"goal": "这一步要达成什么", "done_when": "怎么算完成"}]

要求：
- 最多 6 步，能合并的别拆
- 需要工具的步骤，在 done_when 里写清用什么验证（例如「读到文件内容」「命令输出包含 xxx」）
- 不要写「分析一下」这种无法验证的步骤"""

REPLAN_INSTRUCTION = """前面几步已经做过了，剩下没做完的部分请重新规划。

已完成：
{done}

卡住原因：{failed}

只输出一个 JSON 数组，格式同前，只包含还要做的步骤（不超过 4 步）。"""

SYNTH_INSTRUCTION = """所有步骤都执行完了。现在给用户一个最终答复：
- 直接说结果，不要再复述执行过程
- 有文件或路径就写出来，有数字就给数字
- 如果某步只是部分成功，如实说明
不要调用工具。"""

INTERPLAN_INSTRUCTION = """用户在执行过程中修改了需求，原来的计划不再适用。

已完成的步骤：
{done}

用户的新要求（以此为准）：
{interject}

请按新要求重新规划剩下要做的事，只输出一个 JSON 数组，格式同前，最多 4 步。"""

# 内核自己注入的指令不是用户说的话。重放对话、搜索结果里都要跳过这些。
INTERNAL_PREFIXES = (
    "[",
    "先别动手",
    "前面几步已经做过了",
    "所有步骤都执行完了",
    "继续完成剩下的部分",
)

# 插话转成消息时的模板。说清三件事：这是插话、以新的为准、旧结果还能用但别重复调。
INTERJECT_TEMPLATE = (
    "[用户中途插话] 用户在你执行过程中补充或修改了要求，可能与当前方向不同，"
    "以下面这条为最新要求：\n{text}\n"
    "（已完成的操作结果仍在上下文里，需要时可直接引用，不要为了重新了解而重复调用同样的工具。）"
)


class _Stop(Exception):
    """内核主动终止（模型调用失败等），用于跳出多层循环。"""


def normalize_mode(value: str | None) -> str:
    text = (value or default_mode()).strip().lower()
    return text if text in ("react", "plan") else "react"


def parse_plan(text: str) -> list[dict]:
    """从模型输出里抠出 JSON 计划。抠不出来返回空列表。"""
    start = text.find("[")
    end = text.rfind("]")
    if start == -1 or end <= start:
        return []
    try:
        data = json.loads(text[start : end + 1])
    except json.JSONDecodeError:
        return []
    if not isinstance(data, list):
        return []

    steps: list[dict] = []
    for item in data[:8]:
        if isinstance(item, dict):
            goal = str(item.get("goal") or item.get("step") or "").strip()
            done_when = str(item.get("done_when") or item.get("done") or "").strip()
        elif isinstance(item, str):
            goal, done_when = item.strip(), ""
        else:
            continue
        if goal:
            steps.append({"goal": goal, "done_when": done_when})
    return steps


def _parse_args(raw: str) -> dict:
    try:
        data = json.loads(raw, strict=False) if raw.strip() else {}
        return data if isinstance(data, dict) else {}
    except json.JSONDecodeError:
        return {}


class _Run:
    """一轮任务的运行时：状态、熔断、日志、事件收在一处，两种模式共用。"""

    def __init__(self, agent: "Agent", session: Session, sink: Sink, safe: bool, cancel) -> None:
        self.agent = agent
        self.session = session
        self.sink = sink
        self.safe = safe
        self.cancel = cancel
        self.guard = Guard(agent.limits)
        self.state = TaskState(status="planning", context_budget=agent.context.budget)
        self.step_no = 0
        self.recent_results: list[str] = []
        self.interrupted = False  # 本轮被用户插过话
        self.last_interject = ""  # 最近一次插话原文，重规划时要用

    # ---------------------------------------------------------- 基础

    def stopped(self) -> bool:
        return self.cancel is not None and self.cancel.is_set()

    def emit(self, status: str | None = None, note: str | None = None) -> None:
        if status:
            self.state.status = status
        if note is not None:
            self.state.note = note
        self.sink.state(self.state.snapshot())

    def add_user(self, text: str) -> None:
        self.session.append({"role": "user", "content": text})

    def add_assistant(self, text: str, calls: list | None = None) -> None:
        msg: dict = {"role": "assistant", "content": text or ""}
        if calls:
            msg["tool_calls"] = calls
        self.session.append(msg)

    def add_tool(self, call_id: str, result: str) -> None:
        self.session.append({"role": "tool", "tool_call_id": call_id, "content": result})

    # ---------------------------------------------------------- 中途插话

    def drain_interjects(self) -> bool:
        """把用户中途插话转成消息插进历史。返回这一轮是否收到了插话。

        只在「准备下一次模型请求」时调用，此时上一条 assistant 的 tool_calls
        一定已经配对完整，插 user 消息不会把顺序搞乱。
        """
        items = self.session.take_interjects()
        if not items:
            return False

        for text in items:
            # 插话也是用户输入，同样要过筛查 —— 否则「运行中插一句忽略规则」就绕过了一切
            self._screen(text, "interject")
            self.session.append(
                {"role": "user", "content": INTERJECT_TEMPLATE.format(text=text)}
            )
            self.sink.notice(f"收到中途插话：{text[:60]}")

        # 方向变了，跟旧方向绑定的「原地打转」计数不该继续压着
        self.guard.reset_progress("用户中途修改需求，进度计数已重置")
        self.recent_results.clear()
        self.state.completion = 0.3
        self.state.note = "用户修改了需求方向"
        self.interrupted = True
        self.last_interject = "\n".join(items)
        self.emit("planning")
        return True

    # ---------------------------------------------------------- 调用模型

    def llm(self, tools: list[dict] | None = TOOL_SCHEMAS, stream: bool = True):
        """问一次模型。失败直接终止整轮（错误已经通知过用户）。"""
        self.drain_interjects()  # 发请求前先收一下用户插话
        view, stats = self.agent.context.prepare(self.session.messages)
        self.state.context_tokens = stats.tokens
        self.step_no += 1
        self.state.step = self.step_no
        self.emit("planning")

        on_text = self.sink.text if stream else None
        try:
            text, calls = self.agent.llm.stream(view, tools, on_text=on_text)
        except LLMError as exc:
            if stream:
                self.sink.text_done()
            self.state.status = "error"
            self.emit()
            self.sink.error(str(exc))
            raise _Stop() from exc

        attempts = getattr(self.agent.llm, "last_attempts", 1)
        if attempts > 1:
            self.sink.notice(f"模型重试 {attempts - 1} 次后成功（网络或服务端抖动）。")
        self.state.output_tokens += estimate_text(text)

        if not calls and stream:
            self.sink.text_done()
        return text, calls

    # ---------------------------------------------------------- 执行工具

    def exec_tool(self, call: dict) -> tuple[str, bool, str | None]:
        """返回 (回灌文本, 是否被拒, 熔断原因)。熔断原因非空时调用方应收手。"""
        name = call["function"]["name"]
        raw_args = call["function"].get("arguments") or "{}"
        self.sink.tool_call(name, raw_args)

        args = _parse_args(raw_args)
        blocked = self.guard.before_tool(name, raw_args)
        denial = self.agent.policy.check(name, args)
        # 执行前把 Policy 的白名单同步给沙箱子进程（run_python 守卫要靠它）
        sandbox.set_roots(self.agent.policy.read_roots, self.agent.policy.write_roots)

        if blocked:
            result = f"被熔断拦截：{blocked}。换个思路或直接向用户说明卡在哪。"
            denied = True
        elif denial:
            result = f"被规则拒绝：{denial}。请改用允许范围内的做法。"
            denied = True
        elif self.safe and name in DANGEROUS and not self.sink.confirm(name, raw_args, risk_hint(name, args)):
            result = "用户拒绝执行该操作，请换一种方式或询问用户。"
            denied = True
        else:
            result = execute(name, raw_args)
            denied = False

        result = self.agent.context.compact_tool_result(result)

        self.guard.after_tool(name, raw_args, result, denied=denied)
        self.session.tool_calls += 1
        self.state.tool_calls += 1
        self.recent_results.append(result)
        del self.recent_results[:-4]
        self.state.completion = self.agent.judge.heuristic(self.state, self.recent_results)

        self.sink.tool_result(result)
        self.agent.records.write(step=self.step_no, tool=name, args=raw_args, result=result, blocked=denied)
        self.emit("tooling")
        return result, denied, blocked

    # ---------------------------------------------------------- 收尾

    def finish(self, reason: str) -> str:
        """熔断收手：把「卡在哪」当答复交付。"""
        report = self.guard.report(reason)
        self.add_assistant(report)
        self.state.status = "blocked"
        self.state.note = reason
        self.state.completion = min(self.state.completion, 0.5)
        self.emit()
        self.sink.error(f"已停止：{reason}")
        self.sink.text_done()
        for line in report.splitlines(True):
            self.sink.text(line)
        self.sink.text_done()
        return report

    def stop_out(self) -> str:
        self.emit("stopped")
        self.sink.error("已停止。")
        return ""

    def _screen(self, text: str, source: str = "user") -> None:
        """筛查一段用户输入。命中就审计 + 提示 + 加固系统提示（幂等，只加一次）。"""
        if not text:
            return
        result = safety.screen(text)
        if not result.flagged:
            return
        safety.audit(result, text, source)
        self.sink.notice(result.notice())

        note = result.system_note()
        msgs = self.session.messages
        if not note or not msgs or msgs[0].get("role") != "system":
            return
        current = msgs[0].get("content") or ""
        if "【安全提醒】" not in current:
            msgs[0]["content"] = current + note

    def screen_input(self) -> None:
        for msg in reversed(self.session.messages):
            if msg.get("role") == "user":
                self._screen(str(msg.get("content") or ""), "user")
                return


class Agent:
    """把模型、工具、上下文、边界、熔断、状态串成闭环。"""

    def __init__(
        self,
        llm,
        max_steps: int = 20,
        context: ContextManager | None = None,
        policy: Policy | None = None,
        judge: CompletionJudge | None = None,
        records: RecordLog | None = None,
        limits: Limits | None = None,
        self_check: bool = True,
        max_self_checks: int = 1,
        mode: str | None = None,
        step_rounds: int = 4,
        max_replans: int = 1,
    ) -> None:
        self.llm = llm
        self.max_steps = max_steps
        self.context = context or ContextManager()
        self.policy = policy or Policy.default()
        self.judge = judge or CompletionJudge(llm, enabled=self_check)
        self.records = records if records is not None else RecordLog()
        self.limits = limits or Limits.from_env()
        self.limits.max_steps = max_steps
        self.self_check = self_check
        self.max_self_checks = max_self_checks
        self.mode = normalize_mode(mode)
        self.step_rounds = step_rounds  # 计划模式下单步最多几次模型往返
        self.max_replans = max_replans

    def run(
        self,
        session: Session,
        sink: Sink | None = None,
        safe: bool = False,
        cancel: threading.Event | None = None,
    ) -> str:
        run = _Run(self, session, sink or Sink(), safe, cancel)
        run.screen_input()
        run.emit()
        try:
            if self.mode == "plan":
                return self._run_plan(run)
            return self._run_react(run)
        except _Stop:
            return ""

    # ------------------------------------------------------------ ReAct

    def _run_react(self, run: _Run) -> str:
        self_checks = 0
        while run.step_no < self.max_steps:
            if run.stopped():
                return run.stop_out()
            reason = run.guard.check_stop()
            if reason:
                return run.finish(reason)

            text, calls = run.llm()

            if not calls:
                if self.self_check and self_checks < self.max_self_checks and run.state.tool_calls > 0:
                    run.emit("checking")
                    outcome, payload = self.judge.verify(run.session.messages, TOOL_SCHEMAS)

                    if outcome == "call":
                        # 模型给的是「证据」不是「结论」：真执行，用结果说话
                        verify_calls = payload
                        run.add_assistant(text, verify_calls)
                        failures: list[str] = []
                        for call in verify_calls:
                            result, _denied, blocked = run.exec_tool(call)
                            run.add_tool(call["id"], result)
                            if blocked:
                                return run.finish(blocked)
                            if is_failure(result):
                                failures.append(result)
                        if failures:
                            self_checks += 1
                            brief = failures[0].replace("\n", " ")[:300]
                            run.add_user(
                                f"[验证未通过] 用来核实的操作失败了：{brief}\n"
                                "请修复问题后重新交付，交付前再做一次能证明问题的验证。"
                            )
                            run.state.completion = 0.6
                            run.emit("planning", note="验证未通过，返工中")
                            continue
                        run.state.verified = True
                        run.state.completion = 1.0
                        run.emit("done")
                        return text

                    if outcome == "todo":
                        self_checks += 1
                        run.add_assistant(text)
                        run.add_user(f"继续完成剩下的部分：{payload}")
                        run.state.completion = 0.6
                        run.emit("planning", note=str(payload))
                        continue

                    # noverify（纯问答）或探针失败：按现状放行，但不算已验证
                    run.add_assistant(text)
                    run.state.completion = 1.0 if (text or "").strip() else 0.7
                    if outcome == "noverify" and not payload:
                        run.state.note = "自检探针失败，按现状交付（未经独立验证）"
                    run.emit("done")
                    return text

                run.add_assistant(text)
                run.state.completion = 1.0 if (text or "").strip() else 0.7
                run.emit("done")
                return text

            run.add_assistant(text, calls)
            for call in calls:
                result, _denied, blocked = run.exec_tool(call)
                run.add_tool(call["id"], result)
                if blocked:
                    return run.finish(blocked)
            run.session.steps += 1
            run.sink.step_done(run.step_no)

            if run.stopped():
                return run.stop_out()

        return run.finish(f"达到最大步数 {self.max_steps}")

    # ------------------------------------------------------------ Plan & Execute

    def _run_plan(self, run: _Run) -> str:
        plan = self._make_plan(run)
        if not plan:
            run.sink.notice("计划没解析出来，这一轮改用 ReAct 边做边看。")
            return self._run_react(run)

        run.sink.plan(plan, replanned=False)
        done: list[str] = []
        replans = 0
        index = 0

        while index < len(plan):
            if run.stopped():
                return run.stop_out()
            reason = run.guard.check_stop()
            if reason:
                return run.finish(reason)
            if run.step_no >= self.max_steps:
                return run.finish(f"达到最大步数 {self.max_steps}")

            step = plan[index]
            run.sink.plan_step(index + 1, "running", step["goal"][:60])
            run.add_user(self._step_instruction(step, index + 1, len(plan), done))
            text, failed = self._exec_step(run, index + 1)

            # 用户中途改了需求：这一步的结论作废，剩下的事按新要求重排
            if run.interrupted:
                run.interrupted = False
                run.sink.plan_step(index + 1, "done", "需求已变更，此步作废")
                done.append(f"{step['goal']} → （用户中途改了需求，此步结论作废）")
                if replans < self.max_replans:
                    replans += 1
                    remaining = self._make_plan(run, done=done, interrupted=run.last_interject)
                    if remaining:
                        plan = plan[: index + 1] + remaining
                        run.sink.plan(plan, replanned=True)
                        index += 1
                        continue
                return self._final_answer(run, done)

            if failed:
                run.sink.plan_step(index + 1, "failed", (text or "没做成")[:60])
                if replans < self.max_replans:
                    replans += 1
                    remaining = self._make_plan(run, done=done, failed=text)
                    if remaining:
                        plan = plan[:index] + remaining
                        run.sink.plan(plan, replanned=True)
                        continue
                return run.finish(f"第 {index + 1} 步没做成：{(text or '')[:60]}")

            run.sink.plan_step(index + 1, "done", (text or "完成")[:60])
            done.append(f"{step['goal']} → {(text or '完成').strip()[:120]}")
            index += 1

        return self._final_answer(run, done)

    def _step_instruction(self, step: dict, index: int, total: int, done: list[str]) -> str:
        history = "\n".join(f"- {item}" for item in done[-4:]) or "（还没有）"
        return (
            f"[计划 第 {index}/{total} 步] {step['goal']}\n"
            f"完成标准：{step.get('done_when') or '你自己判断'}\n"
            f"已完成的步骤：\n{history}\n\n"
            "只做这一步，做完用一句话说明结果。需要工具就调，不需要就直接给结果。"
        )

    def _make_plan(
        self,
        run: _Run,
        done: list[str] | None = None,
        failed: str | None = None,
        interrupted: str | None = None,
    ) -> list[dict]:
        done_text = "\n".join(f"- {item}" for item in (done or [])[-4:]) or "（无）"

        if interrupted:
            instruction = INTERPLAN_INSTRUCTION.format(
                done=done_text, interject=interrupted[:600]
            )
        elif failed:
            instruction = REPLAN_INSTRUCTION.format(done=done_text, failed=failed[:200])
        else:
            instruction = PLAN_INSTRUCTION

        run.add_user(instruction)
        text, _calls = run.llm(tools=None, stream=False)
        steps = parse_plan(text)

        if steps:
            brief = "；".join(f"{i + 1}) {s['goal']}" for i, s in enumerate(steps))
            run.add_assistant(f"[计划] {brief}")
        else:
            run.add_assistant("[计划] 未能生成结构化计划")
        return steps

    def _exec_step(self, run: _Run, index: int) -> tuple[str, bool]:
        """执行一步：最多 step_rounds 次模型往返，返回 (结果文本, 是否失败)。"""
        denied_any = False
        ok_tools = 0

        for _ in range(self.step_rounds):
            if run.stopped():
                run.stop_out()
                return "已停止", True
            reason = run.guard.check_stop()
            if reason:
                run.finish(reason)
                return reason, True

            text, calls = run.llm()

            if not calls:
                run.add_assistant(text)
                result = (text or "").strip() or "完成（模型没给说明）"
                # 被拒过且没成功过一次工具，或模型自己说做不到，都算这一步没成
                failed = (denied_any and ok_tools == 0) or any(
                    mark in result for mark in ("做不到", "无法完成", "被拒绝", "失败了")
                )
                return result, failed

            run.add_assistant(text, calls)
            for call in calls:
                result, denied, blocked = run.exec_tool(call)
                run.add_tool(call["id"], result)
                if blocked:
                    run.finish(blocked)
                    return blocked, True
                if denied:
                    denied_any = True
                elif not is_failure(result):
                    ok_tools += 1
            run.session.steps += 1
            run.sink.step_done(run.step_no)

        return "这一步在预算内没做完", True

    def _final_answer(self, run: _Run, done: list[str]) -> str:
        run.add_user(SYNTH_INSTRUCTION)
        text, _calls = run.llm(tools=None)
        if not (text or "").strip():
            text = "\n".join(f"- {item}" for item in done[-6:])
        run.add_assistant(text)
        run.state.completion = 1.0
        run.emit("done")
        return text


def run(
    llm,
    messages: list[dict],
    max_steps: int = 20,
    safe: bool = False,
    sink: Sink | None = None,
    cancel: threading.Event | None = None,
    self_check: bool = True,
    mode: str | None = None,
) -> str:
    """便捷入口：直接对一份消息列表跑一轮（测试和脚本用）。"""
    session = Session()
    session.messages = messages
    agent = Agent(
        llm,
        max_steps=max_steps,
        self_check=self_check,
        records=RecordLog(enabled=False),
        mode=mode,
    )
    return agent.run(session, sink=sink, safe=safe, cancel=cancel)
