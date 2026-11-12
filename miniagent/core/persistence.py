"""核心层：会话持久化。

关窗即失是 Agent 体验的硬伤，这一层把对话落到磁盘：
  .agent/sessions/{id}.jsonl —— 一个会话一个文件，一行一个事件，追加写。

为什么是 JSONL 而不是单个 JSON：追加写永不损坏已写内容，进程崩了、断电了，
最多丢最后一行；恢复只需顺序重放。和 RecordLog 同一套写法。

恢复时做三件修正，保证重放出来的会话永远合法：
  1. 剥掉尾部没配完对的 tool 结果 / 带着工具调用却没有结果的 assistant（崩溃残留）
  2. 丢弃存档里的 system 消息，用当前 system_prompt() 重新生成
     （MCP 工具清单、规则文件都可能已经变了，旧的不作数）
  3. 从消息里重算 steps / tool_calls 统计
"""

from __future__ import annotations

import json
import secrets
import time
from pathlib import Path

from ..config import SESSIONS_DIR
from .session import Session

FORMAT_VERSION = 1
TITLE_CHARS = 24  # 自动标题长度：取首条用户消息前 24 字，不调模型

# 消息里允许入库的字段。tool_calls 的结构由协议层保证可序列化，原样存。
_MSG_FIELDS = ("role", "content", "tool_calls", "tool_call_id", "name")


def new_session_id() -> str:
    """时间戳 + 短随机后缀。同一秒创建多个会话也不会撞名。"""
    return time.strftime("%Y%m%d-%H%M%S") + "-" + secrets.token_hex(2)


def _sanitize(msg: dict) -> dict:
    return {key: msg[key] for key in _MSG_FIELDS if key in msg and msg[key] is not None}


class SessionStore:
    """会话存档：追加写 jsonl，可恢复、可列表。

    用法：
        store = SessionStore()
        store.attach(session)          # 之后 session.append() 的每条消息自动落盘
        ...
        restored = SessionStore.resume("last").load()   # 下次启动接着聊
    """

    def __init__(self, directory: Path | None = None, session_id: str | None = None) -> None:
        self.directory = directory or SESSIONS_DIR
        self.session_id = session_id or new_session_id()
        self.title = ""
        self.enabled = True
        self._path = self.directory / f"{self.session_id}.jsonl"
        self._has_title = False
        self._created = False  # 惰性建文件：第一条消息才落盘，没聊过天的不留空壳

    # ---------------------------------------------------------- 写入

    def _ensure_file(self) -> bool:
        if self._created or not self.enabled:
            return self.enabled
        try:
            self.directory.mkdir(parents=True, exist_ok=True)
            self._write({"type": "meta", "id": self.session_id, "title": self.title})
            self._created = True
        except OSError:
            self.enabled = False
        return self.enabled

    def _write(self, payload: dict) -> None:
        payload["v"] = FORMAT_VERSION
        payload["ts"] = time.strftime("%Y-%m-%d %H:%M:%S")
        with self._path.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(payload, ensure_ascii=False) + "\n")

    def append(self, msg: dict) -> None:
        """消息落盘。首条用户消息顺带生成标题（零成本，不调模型）。"""
        # 标题要在写 meta 之前定好，否则 meta 里的标题永远是空的
        if not self._has_title and not self.title and msg.get("role") == "user":
            title = str(msg.get("content") or "").strip().replace("\n", " ")[:TITLE_CHARS]
            if title:
                self.title = title
                self._has_title = True
        if not self._ensure_file():
            return
        self._write({"type": "msg", **_sanitize(msg)})

    def set_title(self, title: str) -> None:
        """手动改名。追加一行 title，加载时后写的赢。"""
        title = (title or "").strip().replace("\n", " ")[:TITLE_CHARS]
        if not title or not self._ensure_file():
            return
        self.title = title
        self._has_title = True
        self._write({"type": "title", "title": title})

    # ---------------------------------------------------------- 挂接

    def attach(self, session: Session) -> None:
        """把存档挂到会话上：此后每条消息自动追加落盘。"""
        session.attach_observer(self.append)

    # ---------------------------------------------------------- 恢复

    def load(self) -> Session:
        """重放存档成一个可继续对话的 Session（已挂好观察者，接着写同一文件）。"""
        session = Session()
        msgs = self._replay()
        session.messages.extend(msgs)
        session.steps = sum(1 for m in msgs if m.get("role") == "assistant" and m.get("tool_calls"))
        session.tool_calls = sum(1 for m in msgs if m.get("role") == "tool")
        self.attach(session)
        return session

    def _replay(self) -> list[dict]:
        try:
            lines = self._path.read_text(encoding="utf-8").splitlines()
        except OSError:
            return []
        msgs: list[dict] = []
        for line in lines:
            try:
                data = json.loads(line)
            except json.JSONDecodeError:
                continue  # 最后一行写了一半之类，跳过
            if not isinstance(data, dict) or data.get("type") != "msg":
                if data.get("type") == "title":
                    self.title = str(data.get("title") or "")
                    self._has_title = bool(self.title)
                continue
            if data.get("role") == "system":
                continue  # 系统提示词恢复时重新生成，不用存档里的
            msgs.append(_sanitize(data))
        return self._repair(msgs)

    @staticmethod
    def _repair(msgs: list[dict]) -> list[dict]:
        """崩溃只会截断尾部，所以只修尾部：剥掉配不上对的工具消息。"""
        while msgs and msgs[-1].get("role") == "tool":
            msgs.pop()
        last = msgs[-1] if msgs else None
        if last and last.get("role") == "assistant" and last.get("tool_calls"):
            msgs.pop()
        return msgs

    # ---------------------------------------------------------- 列表与定位

    @staticmethod
    def list_sessions(directory: Path | None = None, limit: int = 30) -> list[dict]:
        """按修改时间倒序列出会话。字段：id / title / turns / updated / path。"""
        target = directory or SESSIONS_DIR
        if not target.is_dir():
            return []
        items: list[dict] = []
        for path in target.glob("*.jsonl"):
            info = _read_head(path)
            if info is None:
                continue
            info["updated"] = path.stat().st_mtime
            info["path"] = path
            items.append(info)
        items.sort(key=lambda item: item["updated"], reverse=True)
        return items[:limit]

    @classmethod
    def resume(cls, ref: str | None = "last", directory: Path | None = None) -> "SessionStore":
        """按 ID 或 "last" 定位一个已有会话。找不到就抛 FileNotFoundError。"""
        target = directory or SESSIONS_DIR
        if ref and ref != "last":
            path = target / f"{ref}.jsonl"
            if not path.is_file():
                path = target / f"{ref}"  # 允许直接传文件路径
                if not path.is_file():
                    raise FileNotFoundError(f"没有找到会话：{ref}")
            return cls(directory=path.parent, session_id=path.stem)
        sessions = cls.list_sessions(target, limit=1)
        if not sessions:
            raise FileNotFoundError("还没有任何历史会话。")
        return cls(directory=sessions[0]["path"].parent, session_id=sessions[0]["id"])

    @property
    def path(self) -> Path:
        return self._path


def _read_head(path: Path) -> dict | None:
    """读会话文件的头两行（meta + 首条消息），凑出列表用的摘要。"""
    title = ""
    session_id = path.stem
    turns = 0
    try:
        with path.open("r", encoding="utf-8") as fh:
            for index, line in enumerate(fh):
                try:
                    data = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if not isinstance(data, dict):
                    continue
                kind = data.get("type")
                if kind == "meta":
                    title = str(data.get("title") or "")
                elif kind == "title":
                    title = str(data.get("title") or "")
                elif kind == "msg" and data.get("role") == "user":
                    turns += 1
                if index > 4000:  # 防御异常大文件，列表页不需要全量统计
                    break
    except OSError:
        return None
    return {"id": session_id, "title": title or "（未命名）", "turns": turns}
