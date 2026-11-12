"""交互层：搜索窗口。

数据收集和匹配逻辑在 `core/search.py`，这里只负责显示和跳转。
窗口骨架（头部/摘要/列表/详情）来自 ListDialog，这里只留搜索自己的逻辑。
"""

from __future__ import annotations

import tkinter as tk

from ..core.search import KINDS, MAX_HITS, Hit, collect, counts as count_kinds, search
from . import theme
from .list_dialog import ListDialog
from .widgets import Pill


class SearchDialog(ListDialog):
    TITLE = "搜索"
    SIZE = (720, 580)
    MINSIZE = (580, 440)
    DETAIL_HINT = "选中一条看详情。对话按回车或双击可以跳过去。"

    def __init__(self, master: tk.Misc, session, on_jump=None) -> None:
        self.session = session
        self.on_jump = on_jump
        self.all_hits: list[Hit] = collect(session)
        self.results: list[Hit] = []
        self._selected: Hit | None = None
        self.kind: str | None = None  # None = 全部
        self._chips: dict[str | None, Pill] = {}
        super().__init__(master)

    # ------------------------------------------------------------ 结构

    def _configure_tags(self) -> None:
        self.list.tag_configure("kind_chat", foreground=theme.ACCENT_DEEP, font=theme.FONT_SM)
        self.list.tag_configure("kind_artifact", foreground=theme.SUCCESS, font=theme.FONT_SM)
        self.list.tag_configure("kind_task", foreground=theme.TEXT_FAINT, font=theme.FONT_SM)
        self.list.tag_configure("hit", foreground=theme.TEXT, font=theme.FONT_BODY)
        self.list.tag_configure("empty", foreground=theme.TEXT_GHOST, font=theme.FONT_SM)
        self.list.tag_bind("hit", "<Double-Button-1>", lambda e: self._jump())

    def _build_middle(self) -> None:
        """分类条：全部 / 对话 / 任务 / 产物，各带条数。"""
        row = tk.Frame(self, bg=theme.BG)
        row.pack(fill="x", padx=20, pady=(0, 8))
        for kind in (None, *KINDS):
            chip = Pill(
                row,
                text=kind or "全部",
                command=lambda k=kind: self.set_kind(k),
                variant="primary" if kind is None else "secondary",
                bg=theme.BG,
                height=28,
                padx=12,
            )
            chip.pack(side="left", padx=(0, 8))
            self._chips[kind] = chip

    # ------------------------------------------------------------ 交互

    def set_kind(self, kind: str | None) -> None:
        self.kind = kind
        for key, chip in self._chips.items():
            chip.set_style(variant="primary" if key == kind else "secondary")
        self.refresh(self.entry.get())

    def _refresh_chip_labels(self) -> None:
        tally = count_kinds(self.all_hits)
        for kind, chip in self._chips.items():
            chip.set_text(f"{kind or '全部'} {tally[kind] if kind else len(self.all_hits)}")

    def on_entry_return(self) -> None:
        self._jump()

    def on_item_click(self, event) -> None:
        hit = self._hit_at(event)
        if hit is None:
            return
        self._selected = hit
        text = hit.detail if len(hit.detail) <= 400 else hit.detail[:400] + "…"
        hint = "\n\n（回车或双击跳到这条对话）" if hit.kind == "对话" else ""
        self.detail.configure(text=text + hint, fg=theme.TEXT_MUTED)

    # ------------------------------------------------------------ 内容

    def populate(self) -> None:
        self._refresh_chip_labels()
        self.refresh("")

    def refresh(self, keyword: str) -> None:
        self._refresh_chip_labels()
        keyword = keyword.strip()

        if keyword:
            self.results = search(
                self.all_hits, keyword, kinds=[self.kind] if self.kind else None
            )
        elif self.kind:
            # 只选了分类没输关键词：把这一类直接列出来，等于「浏览」
            self.results = [hit for hit in self.all_hits if hit.kind == self.kind][:MAX_HITS]
        else:
            self.results = []

        self.reset_list()

        tally = count_kinds(self.all_hits)
        scope = f"「{self.kind}」里" if self.kind else ""

        if not keyword and not self.kind:
            self.summary.configure(
                text="共 {} 条可搜内容：对话 {} · 任务 {} · 产物 {}".format(
                    len(self.all_hits), tally["对话"], tally["任务"], tally["产物"]
                )
            )
            self.list.insert("end", "输入关键词开始搜，或者点上面的分类直接浏览。\n", "empty")
        elif not self.results:
            self.summary.configure(text=f"{scope}没有匹配「{keyword}」的内容")
            self.list.insert("end", "换个关键词，或者把分类切到「全部」。\n", "empty")
        else:
            found = count_kinds(self.results)
            title = f"{scope}找到 {len(self.results)} 条" if keyword else f"{scope}共 {len(self.results)} 条"
            self.summary.configure(
                text="{}：对话 {} · 任务 {} · 产物 {}".format(
                    title, found["对话"], found["任务"], found["产物"]
                )
            )
            for index, hit in enumerate(self.results):
                tag = {"对话": "kind_chat", "产物": "kind_artifact"}.get(hit.kind, "kind_task")
                self.list.insert("end", f"[{hit.kind}] ", (tag, f"hit_{index}"))
                self.list.insert("end", f"{hit.title}\n", ("hit", f"hit_{index}"))

        self.freeze_list()

    def _hit_at(self, event) -> Hit | None:
        index = self.list.index(f"@{event.x},{event.y}")
        for tag in self.list.tag_names(index):
            if tag.startswith("hit_"):
                try:
                    return self.results[int(tag[4:])]
                except (ValueError, IndexError):
                    return None
        return None

    def _jump(self) -> None:
        hit = self._selected
        if hit is None or hit.kind != "对话":
            hit = next((h for h in self.results if h.kind == "对话"), None)
        if hit is None:
            return
        if self.on_jump and hit.anchor:
            self.on_jump(hit.anchor)
        self.destroy()
