"""交互层：命令行界面。

只做两件事：把 Agent 事件打到终端、从终端读输入。
"""

from __future__ import annotations

import json
import sys

from ..config import APP_NAME
from ..core import Agent, Session, SessionStore, Sink
from ..core.completion import VERIFY_PROMPT
from ..core.loop import normalize_mode
from ..skills import manager as skills
from ..tools.checkpoints import undo_last

DIM = "\033[2m"
CYAN = "\033[36m"
GREEN = "\033[32m"
RED = "\033[31m"
YELLOW = "\033[33m"
RESET = "\033[0m"


class ConsoleSink(Sink):
    def __init__(self) -> None:
        self._open = False
        self.last_state: dict | None = None

    def text(self, piece: str) -> None:
        self._open = True
        sys.stdout.write(piece)
        sys.stdout.flush()

    def text_done(self) -> None:
        if self._open:
            sys.stdout.write("\n")
            sys.stdout.flush()
            self._open = False

    def tool_call(self, name: str, args: str) -> None:
        preview = args if len(args) <= 300 else args[:300] + "..."
        print(f"{DIM}  ↳ {name} {preview}{RESET}")

    def tool_result(self, result: str) -> None:
        lines = result.splitlines()
        preview = "\n".join(lines[:6])
        if len(lines) > 6:
            preview += f"\n  ...（共 {len(lines)} 行）"
        print(f"{DIM}{preview}{RESET}")

    def step_done(self, step: int) -> None:
        print(f"{DIM}  ── 第 {step} 步完成 ──{RESET}")

    def state(self, snapshot: dict) -> None:
        self.last_state = snapshot

    def notice(self, message: str) -> None:
        print(f"{YELLOW}· {message}{RESET}")

    def plan(self, steps: list[dict], replanned: bool = False) -> None:
        head = "重规划：" if replanned else "计划："
        print(f"{CYAN}{head}{RESET}")
        for index, step in enumerate(steps[:8], 1):
            print(f"{DIM}  {index}. {step.get('goal', '')}{RESET}")

    def plan_step(self, index: int, status: str, note: str = "") -> None:
        mark = {"running": "…", "done": "✓", "failed": "✗"}.get(status, "·")
        color = GREEN if status == "done" else RED if status == "failed" else DIM
        print(f"{color}  {mark} 第 {index} 步 {note}{RESET}")

    def error(self, message: str) -> None:
        print(f"{RED}{message}{RESET}")

    def confirm(self, name: str, args: str, hint: str = "") -> bool:
        suffix = f"（{hint}）" if hint else ""
        print(f"{YELLOW}需要确认：{name}{suffix} {args[:300]}{RESET}")
        try:
            return input("执行？[y/N] ").strip().lower() in ("y", "yes")
        except (EOFError, KeyboardInterrupt):
            return False


def print_state(sink: ConsoleSink) -> None:
    """收尾时把上下文与 token 用量报一行。"""
    st = sink.last_state
    if not st:
        return
    verified = " · 已验证" if st.get("verified") else ""
    print(
        f"{DIM}上下文 {st['context_ratio'] * 100:.0f}% "
        f"({st['context_tokens']} / {st['context_budget']} tok) · "
        f"输出约 {st['output_tokens']} tok · 完成度 {st['completion'] * 100:.0f}%{verified}{RESET}"
    )


def one_shot(
    llm, prompt: str, max_steps: int = 20, safe: bool = False,
    mode: str | None = None, session: Session | None = None,
) -> bool:
    session = session or Session()
    session.add_user(prompt)
    sink = ConsoleSink()
    answer = Agent(llm, max_steps=max_steps, mode=mode).run(session, sink=sink, safe=safe)
    if answer:
        print()
    print_state(sink)
    return bool(answer)


def repl(
    llm, max_steps: int = 20, safe: bool = False, mode: str | None = None,
    session: Session | None = None, store: "SessionStore | None" = None,
) -> None:
    print(
        f"{CYAN}{APP_NAME}{RESET}  模型 {getattr(llm, 'label', llm.model)}  "
        f"{DIM}模式 {normalize_mode(mode)}  (exit 退出，/undo 撤销写入，/skill 地址装技能，/clear 新开一轮，历史自动存档){RESET}"
    )
    if session is None:
        session = Session()
        if store is None:
            store = SessionStore()
        store.attach(session)
    agent = Agent(llm, max_steps=max_steps, mode=mode)
    while True:
        try:
            prompt = input(f"\n{GREEN}你 ›{RESET} ").strip()
        except (EOFError, KeyboardInterrupt):
            print("\n再见。")
            return
        if not prompt:
            continue
        if prompt.lower() in ("exit", "quit", "/exit"):
            print("再见。")
            return
        if prompt == "/undo":
            print(f"{DIM}{undo_last()}{RESET}")
            continue
        if prompt.startswith("/skill "):
            source = prompt[len("/skill "):].strip()
            try:
                keys = skills.install(source)
                print(f"{GREEN}已装 {len(keys)} 个技能：{'、'.join(keys)}{RESET}")
            except Exception as exc:  # noqa: BLE001
                print(f"{RED}安装失败：{exc}{RESET}")
            continue
        if prompt == "/clear":
            # 新开一轮 = 新的存档文件；旧会话留在历史里，可以 --resume 找回
            session.detach_observer()
            session.reset()
            if store is not None:
                store = SessionStore()
                store.attach(session)
            print(f"{DIM}已开新会话，旧会话已存档。{RESET}")
            continue

        session.add_user(prompt)
        print(f"{CYAN}助手 ›{RESET} ", end="", flush=True)
        sink = ConsoleSink()
        if agent.run(session, sink=sink, safe=safe):
            print()
        print_state(sink)


# ---------------------------------------------------------------- 自检


class _FakeLLM:
    """脚本化假模型：先要一个工具，拿到结果再给答案。"""

    model = "fake-scripted"

    def __init__(self) -> None:
        self.turns = 0

    def stream(self, messages, tools=None, on_text=None):
        last = messages[-1].get("content") if messages else ""
        if tools is None or last == VERIFY_PROMPT:  # 出口探针（含验证探针），直接答完成
            return "DONE", []
        self.turns += 1
        if not any(m.get("role") == "tool" for m in messages):
            payload = json.dumps({"command": "echo hello-agent"})
            return "", [
                {"id": "call_selftest", "type": "function", "function": {"name": "run_shell", "arguments": payload}}
            ]
        tool_msg = [m for m in messages if m.get("role") == "tool"][-1]["content"]
        assert "hello-agent" in tool_msg, f"工具结果异常：{tool_msg}"
        answer = "自检通过：模型→工具→回灌→答复 链路正常。"
        if on_text:
            on_text(answer)
        return answer, []


def selftest() -> int:
    print(f"{CYAN}[selftest] 开始{RESET}")
    fake = _FakeLLM()
    session = Session()
    session.add_user("自检")
    answer = Agent(fake, max_steps=5).run(session, sink=ConsoleSink())
    checks = [
        ("循环执行了 2 轮", fake.turns == 2),
        ("final answer 非空", bool(answer)),
        ("历史里带 tool 消息", any(m.get("role") == "tool" for m in session.messages)),
        ("tool_call_id 正确", any(m.get("tool_call_id") == "call_selftest" for m in session.messages)),
        ("统计到了工具次数", session.tool_calls == 1),
    ]
    for label, ok in checks:
        print(f"  {'PASS' if ok else 'FAIL'}  {label}")
    ok_all = all(ok for _, ok in checks)
    print(f"{GREEN if ok_all else RED}[selftest] {'全部通过' if ok_all else '存在失败项'}{RESET}")
    return 0 if ok_all else 1
