"""会话持久化测试：落盘、恢复、崩溃修复、列表、与内核闭环的集成。

不需要网络，`python tests/test_persistence.py` 直接跑。
"""

from __future__ import annotations

import json
import os
import sys
import tempfile
import time
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from miniagent.core import Agent, Session, SessionStore, Sink  # noqa: E402
from miniagent.core.context import RecordLog  # noqa: E402


def tool_call(name: str, args: dict, call_id: str = "call_1") -> dict:
    return {"id": call_id, "type": "function", "function": {"name": name, "arguments": json.dumps(args)}}


class ScriptLLM:
    """按脚本回放；tools=None 的那次调用当作出口自评。"""

    def __init__(self, replies: list[tuple[str, list[dict]]], probe: str = "DONE") -> None:
        self.replies = list(replies)
        self.probe = probe

    def stream(self, messages, tools=None, on_text=None):
        if tools is None:
            return self.probe, []
        if not self.replies:
            return "结束", []
        return self.replies.pop(0)


def _read_lines(path: Path) -> list[dict]:
    out = []
    for line in path.read_text(encoding="utf-8").splitlines():
        try:
            out.append(json.loads(line))
        except json.JSONDecodeError:
            pass
    return out


def test_append_and_lazy() -> list[tuple[str, bool]]:
    checks = []
    with tempfile.TemporaryDirectory() as tmp:
        store = SessionStore(directory=Path(tmp))
        checks.append(("没聊过天不建文件", not (Path(tmp) / f"{store.session_id}.jsonl").exists()))

        session = Session()
        store.attach(session)
        session.add_user("帮我看看这个项目怎么跑起来")
        path = Path(tmp) / f"{store.session_id}.jsonl"
        checks.append(("首条消息触发建文件", path.is_file()))
        data = _read_lines(path)
        checks.append(("meta 在最前", data[0].get("type") == "meta" and data[0].get("id") == store.session_id))
        checks.append(("meta 带自动标题", data[0].get("title") == "帮我看看这个项目怎么跑起来"))
        checks.append(("消息行带 role/content", data[1].get("role") == "user" and "项目" in data[1].get("content", "")))

        # 模型回复 + 工具结果也要落
        session.append({"role": "assistant", "content": "", "tool_calls": [tool_call("list_dir", {"path": "."})]})
        session.append({"role": "tool", "tool_call_id": "call_1", "content": "a.py\nb.py"})
        data = _read_lines(path)
        checks.append(("assistant 带工具调用", data[2].get("role") == "assistant" and len(data[2]["tool_calls"]) == 1))
        checks.append(("tool 结果落盘", data[3].get("role") == "tool" and data[3]["tool_call_id"] == "call_1"))
    return checks


def test_set_title() -> list[tuple[str, bool]]:
    checks = []
    with tempfile.TemporaryDirectory() as tmp:
        store = SessionStore(directory=Path(tmp))
        session = Session()
        store.attach(session)
        session.add_user("第一条用户消息")
        store.set_title("重构 loop 主循环")
        path = Path(tmp) / f"{store.session_id}.jsonl"
        restored = store.load()
        checks.append(("后写的标题生效", restored is not None and store.title == "重构 loop 主循环"))
        checks.append(("title 行已落盘", any(d.get("type") == "title" for d in _read_lines(path))))
    return checks


def test_load_roundtrip() -> list[tuple[str, bool]]:
    checks = []
    with tempfile.TemporaryDirectory() as tmp:
        store = SessionStore(directory=Path(tmp))
        session = Session()
        store.attach(session)
        session.add_user("问题 A")
        session.append({"role": "assistant", "content": "", "tool_calls": [tool_call("list_dir", {}, "c1")]})
        session.append({"role": "tool", "tool_call_id": "c1", "content": "结果"})
        session.append({"role": "assistant", "content": "答案 A"})

        restored = store.load()
        msgs = restored.messages
        checks.append(("系统提示是新生成的", msgs[0]["role"] == "system" and msgs[0]["content"] != "存档里的"))
        checks.append(("存档里的 system 被丢弃", sum(1 for m in msgs if m["role"] == "system") == 1))
        checks.append(("用户消息恢复", any(m.get("content") == "问题 A" for m in msgs)))
        calls = [m for m in msgs if m["role"] == "assistant" and m.get("tool_calls")]
        checks.append(("工具调用恢复", len(calls) == 1 and calls[0]["tool_calls"][0]["function"]["name"] == "list_dir"))
        checks.append(("工具结果恢复", any(m.get("tool_call_id") == "c1" for m in msgs)))
        checks.append(("steps 重算", restored.steps == 1))
        checks.append(("tool_calls 重算", restored.tool_calls == 1))
        checks.append(("恢复的会话已挂观察者", restored._observer is not None))
    return checks


def test_crash_repair() -> list[tuple[str, bool]]:
    checks = []
    with tempfile.TemporaryDirectory() as tmp:
        store = SessionStore(directory=Path(tmp))
        session = Session()
        store.attach(session)
        session.add_user("跑个任务")
        session.append({"role": "assistant", "content": "", "tool_calls": [tool_call("list_dir", {}, "c1"), tool_call("list_dir", {}, "c2")]})
        session.append({"role": "tool", "tool_call_id": "c1", "content": "一半结果"})  # c2 没回来就崩了
        session.append({"role": "tool", "tool_call_id": "c2", "content": "另一半"})

        restored = store.load()
        msgs = restored.messages
        checks.append(("完整配对的消息保留", any(m.get("content") == "跑个任务" for m in msgs)))
        checks.append(("尾部孤儿 tool 被剥掉", not any(m.get("role") == "tool" for m in msgs)))

        # 再造一种：assistant 带了 tool_calls，结果一条都没回来
        store2 = SessionStore(directory=Path(tmp) / "b")
        s2 = Session()
        store2.attach(s2)
        s2.add_user("另一个任务")
        s2.append({"role": "assistant", "content": "", "tool_calls": [tool_call("list_dir", {}, "c9")]})
        restored2 = store2.load()
        checks.append(("无结果的 assistant 被剥掉", all(not m.get("tool_calls") for m in restored2.messages)))
    return checks


def test_bad_tail_line() -> list[tuple[str, bool]]:
    checks = []
    with tempfile.TemporaryDirectory() as tmp:
        store = SessionStore(directory=Path(tmp))
        session = Session()
        store.attach(session)
        session.add_user("你好")
        path = Path(tmp) / f"{store.session_id}.jsonl"
        with path.open("a", encoding="utf-8") as fh:  # 模拟断电：最后一行写了一半
            fh.write('{"type":"msg","role":"user","cont')
        restored = store.load()
        checks.append(("半截行被跳过", len(restored.messages) == 2))
        checks.append(("恢复后还能继续聊", restored.messages[1]["content"] == "你好"))
    return checks


def test_observer_failure() -> list[tuple[str, bool]]:
    checks = []

    def broken(_msg: dict) -> None:
        raise RuntimeError("磁盘炸了")

    session = Session()
    session.attach_observer(broken)
    session.add_user("第一条")
    session.add_user("第二条")
    checks.append(("观察者异常不影响对话", len(session.messages) == 3))
    checks.append(("坏观察者被摘除", session._observer is None))
    return checks


def test_list_and_resume() -> list[tuple[str, bool]]:
    checks = []
    with tempfile.TemporaryDirectory() as tmp:
        target = Path(tmp)
        old = SessionStore(directory=target, session_id="20260101-000000-aaaa")
        s1 = Session()
        old.attach(s1)
        s1.add_user("旧会话")

        time.sleep(0.02)  # 保证 mtime 有先后
        new = SessionStore(directory=target, session_id="20260102-000000-bbbb")
        s2 = Session()
        new.attach(s2)
        s2.add_user("第一问")
        s2.add_user("第二问")

        items = SessionStore.list_sessions(target)
        checks.append(("列表倒序（新的在前）", len(items) == 2 and items[0]["id"] == new.session_id))
        checks.append(("turns 计数正确", items[0]["turns"] == 2))
        checks.append(("标题进入列表", items[1]["title"] == "旧会话"))

        resumed = SessionStore.resume("last", directory=target)
        checks.append(("resume last 定位最新", resumed.session_id == new.session_id))
        resumed_by_id = SessionStore.resume("20260101-000000-aaaa", directory=target)
        checks.append(("resume 按 ID 定位", resumed_by_id.session_id == old.session_id))
        try:
            SessionStore.resume("不存在的ID", directory=target)
            checks.append(("找不到的 ID 报错", False))
        except FileNotFoundError:
            checks.append(("找不到的 ID 报错", True))
    return checks


def test_loop_integration() -> list[tuple[str, bool]]:
    """整条闭环跑一遍，确认 run() 路径上的每条消息都落了盘。"""
    checks = []
    with tempfile.TemporaryDirectory() as tmp:
        store = SessionStore(directory=Path(tmp))
        session = Session()
        store.attach(session)
        session.add_user("列出目录里的文件")
        llm = ScriptLLM(
            replies=[("", [tool_call("list_dir", {"path": "."}, "call_x")]), ("目录里有 tests 和 miniagent。", [])]
        )
        agent = Agent(llm, max_steps=5, self_check=False, records=RecordLog(enabled=False))
        answer = agent.run(session, sink=Sink())
        checks.append(("闭环跑完有答复", "tests" in answer))

        data = _read_lines(Path(tmp) / f"{store.session_id}.jsonl")
        roles = [d.get("role") for d in data if d.get("type") == "msg"]
        checks.append(("用户消息落盘", "user" in roles))
        checks.append(("assistant 带工具调用落盘", any(d.get("role") == "assistant" and d.get("tool_calls") for d in data)))
        checks.append(("工具结果落盘", "tool" in roles))
        checks.append(("最终答复落盘", roles.count("assistant") >= 2))

        restored = store.load()
        checks.append(("恢复后能接着跑", restored.turns == 1 and restored.tool_calls == 1))
    return checks


def main() -> int:
    print("[persistence] 开始")
    checks = (
        test_append_and_lazy()
        + test_set_title()
        + test_load_roundtrip()
        + test_crash_repair()
        + test_bad_tail_line()
        + test_observer_failure()
        + test_list_and_resume()
        + test_loop_integration()
    )
    for label, ok in checks:
        print(f"  {'PASS' if ok else 'FAIL'}  {label}")
    ok_all = all(ok for _, ok in checks)
    print(f"[persistence] {'全部通过' if ok_all else '存在失败项'}")
    return 0 if ok_all else 1


if __name__ == "__main__":
    raise SystemExit(main())
