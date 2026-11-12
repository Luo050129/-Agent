"""交互层：「列表 + 详情」窗口公共骨架。

SearchDialog（搜索）和 HistoryDialog（历史会话）是同一副骨架：
头部（搜索框或标题）→ 摘要行 → 可选中段（分类筛选）→ 列表 → 底部详情。
差异只在数据来源、条目标签样式和交互行为。骨架收在这里，两个窗口各自
只留数据逻辑。

注意：标签样式要读当前主题值，主题切换是运行期改写 theme 模块的全局量，
所以样式必须在窗口构建时取（_configure_tags），不能存成类属性。
"""

from __future__ import annotations

import tkinter as tk

from . import theme
from .widgets import Pill


class ListDialog(tk.Toplevel):
    TITLE = ""
    SIZE = (720, 580)
    MINSIZE = (580, 440)
    WITH_ENTRY = True  # 头部放搜索框（搜索窗）还是纯标题（历史窗）
    DETAIL_HINT = "选中一条看详情。"
    DETAIL_WRAP = 620

    def __init__(self, master: tk.Misc) -> None:
        super().__init__(master, bg=theme.BG)
        self.title(self.TITLE)
        self.geometry(theme.dialog_geometry(self, *self.SIZE))
        self.minsize(theme.scale_px(self.MINSIZE[0]), theme.scale_px(self.MINSIZE[1]))
        self.transient(master)

        self._build_head()
        self._build_middle()
        # 详情区先 pack：列表是 expand 的，后 pack 的会被挤成一条线
        self._build_detail()
        self._build_list()
        self.populate()

    # ------------------------------------------------------------ 骨架

    def _build_head(self) -> None:
        head = tk.Frame(self, bg=theme.BG)
        head.pack(fill="x", padx=20, pady=(16, 0))

        if self.WITH_ENTRY:
            self.entry = tk.Entry(
                head,
                bg=theme.BG_SOFT,
                fg=theme.TEXT,
                insertbackground=theme.TEXT,
                relief="flat",
                font=theme.FONT_BODY,
                highlightthickness=1,
                highlightbackground=theme.BORDER_STRONG,
                highlightcolor=theme.ACCENT,
            )
            self.entry.pack(side="left", fill="x", expand=True, ipady=7)
            self.entry.bind("<KeyRelease>", lambda e: self.refresh(self.entry.get()))
            self.entry.bind("<Return>", lambda e: self.on_entry_return())
            self.entry.bind("<Escape>", lambda e: self.destroy())
            self.entry.focus_set()
        else:
            tk.Label(
                head, text=self.TITLE, bg=theme.BG, fg=theme.TEXT, font=theme.FONT_H2, anchor="w"
            ).pack(side="left")

        Pill(head, text="关闭", command=self.destroy, variant="secondary", bg=theme.BG).pack(
            side="right", padx=(10, 0)
        )

        self.summary = tk.Label(
            self, text="", bg=theme.BG, fg=theme.TEXT_FAINT, font=theme.FONT_SM, anchor="w"
        )
        self.summary.pack(fill="x", padx=22, pady=(8, 4))

    def _build_middle(self) -> None:
        """子类钩子：往头部和列表之间塞内容（如搜索的分类筛选条）。"""

    def _build_detail(self) -> None:
        bottom = tk.Frame(self, bg=theme.BG)
        bottom.pack(fill="x", side="bottom", padx=20, pady=(10, 16))
        self.detail = tk.Label(
            bottom,
            text=self.DETAIL_HINT,
            bg=theme.BG_CARD,
            fg=theme.TEXT_MUTED,
            font=theme.FONT_SM,
            anchor="w",
            justify="left",
            wraplength=self.DETAIL_WRAP,
            padx=12,
            pady=10,
        )
        self.detail.pack(fill="x")

    def _build_list(self) -> None:
        wrap = tk.Frame(self, bg=theme.BG)
        wrap.pack(fill="both", expand=True, padx=20)

        self.list = tk.Text(
            wrap,
            bg=theme.BG,
            fg=theme.TEXT,
            relief="flat",
            font=theme.FONT_BODY,
            wrap="word",
            cursor="arrow",
            padx=8,
            pady=6,
            highlightthickness=0,
        )
        self.list.pack(side="left", fill="both", expand=True)
        bar = tk.Scrollbar(wrap, command=self.list.yview, width=10)
        bar.pack(side="right", fill="y")
        self.list.configure(yscrollcommand=bar.set, state="disabled")

        self._configure_tags()
        # 单击统一走 on_item_click 钩子（命中不了条目时子类自己早退）。
        # 双击 / 回车的绑定各窗口差异大（搜索窗绑在 hit 标签上、历史窗绑整表），
        # 由子类在 _configure_tags 里按需绑。
        if type(self).on_item_click is not ListDialog.on_item_click:
            self.list.bind("<Button-1>", self.on_item_click)
        self.list.bind("<Escape>", lambda e: self.destroy())

    def _configure_tags(self) -> None:
        """子类重写：给自己列表里的 Text 标签配样式。"""

    # ------------------------------------------------------------ 子类接口

    def refresh(self, keyword: str) -> None:
        """搜索框每次按键都会调；无搜索框的窗口只在 populate 时自己调。"""

    def populate(self) -> None:
        """窗口建好后填一次内容。子类必须重写。"""

    def on_entry_return(self) -> None:
        """搜索框里按回车。"""

    def on_item_click(self, event) -> None:
        """单击列表条目。"""

    # ------------------------------------------------------------ 小工具

    def reset_list(self) -> None:
        """清空列表并允许写入。"""
        self.list.configure(state="normal")
        self.list.delete("1.0", "end")

    def freeze_list(self) -> None:
        self.list.configure(state="disabled")

    def show_empty(self, text: str) -> None:
        self.reset_list()
        self.list.insert("end", text + "\n", "empty")
        self.freeze_list()
