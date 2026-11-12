"""交互层：历史会话窗口。

数据来自 `core/persistence.py` 的 SessionStore.list_sessions()，
这里只负责展示和触发恢复。窗口骨架来自 ListDialog。
"""

from __future__ import annotations

import time
import tkinter as tk
from pathlib import Path

from ..core.persistence import SessionStore
from . import theme
from .list_dialog import ListDialog


def _fmt_time(ts: float) -> str:
    return time.strftime("%m-%d %H:%M", time.localtime(ts))


class HistoryDialog(ListDialog):
    TITLE = "历史会话"
    SIZE = (640, 520)
    MINSIZE = (480, 380)
    WITH_ENTRY = False
    DETAIL_HINT = "选中一条看详情，回车或双击恢复这段对话。"
    DETAIL_WRAP = 560

    def __init__(self, master: tk.Misc, on_restore=None) -> None:
        self.on_restore = on_restore
        self.sessions: list[dict] = SessionStore.list_sessions()
        self._selected: dict | None = None
        super().__init__(master)

    # ------------------------------------------------------------ 结构

    def _configure_tags(self) -> None:
        self.list.tag_configure("title", foreground=theme.TEXT, font=theme.FONT_BODY)
        self.list.tag_configure("meta", foreground=theme.TEXT_FAINT, font=theme.FONT_SM)
        self.list.tag_configure("empty", foreground=theme.TEXT_GHOST, font=theme.FONT_SM)
        self.list.tag_bind("item", "<Double-Button-1>", lambda e: self._restore())
        self.list.bind("<Return>", lambda e: self._restore())

    # ------------------------------------------------------------ 交互

    def on_item_click(self, event) -> None:
        item = self._item_at(event)
        if item is None:
            return
        self._selected = item
        updated = _fmt_time(item["updated"])
        self.detail.configure(
            text=f"{item['title']}\n会话 {item['id']} · {item['turns']} 轮对话 · 最近活动 {updated}\n"
            "回车或双击恢复这段对话，接着聊会写回同一个存档。",
            fg=theme.TEXT_MUTED,
        )

    def _item_at(self, event) -> dict | None:
        index = self.list.index(f"@{event.x},{event.y}")
        for tag in self.list.tag_names(index):
            if tag.startswith("item_"):
                try:
                    return self.sessions[int(tag[5:])]
                except (ValueError, IndexError):
                    return None
        return None

    def _restore(self) -> None:
        item = self._selected
        if item is None and self.sessions:
            item = self.sessions[0]
        if item is None:
            return
        if self.on_restore:
            self.on_restore(SessionStore(directory=Path(item["path"]).parent, session_id=item["id"]))
        self.destroy()

    # ------------------------------------------------------------ 内容

    def populate(self) -> None:
        self.reset_list()
        if not self.sessions:
            self.summary.configure(text="还没有历史会话。聊过天的内容都会自动存档到这里。")
            self.show_empty("随便发一条消息，关掉窗口再回来，这里就有了。")
            return

        self.summary.configure(text=f"共 {len(self.sessions)} 个会话，按最近使用排序。")
        for index, item in enumerate(self.sessions):
            marks = (f"item_{index}", "item")
            self.list.insert("end", f"{item['title']}\n", marks + ("title",))
            self.list.insert(
                "end",
                f"    {item['turns']} 轮对话 · 最近 {_fmt_time(item['updated'])}\n",
                marks + ("meta",),
            )
        self.freeze_list()
