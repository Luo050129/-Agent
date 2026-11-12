"""交互层：图形界面（侧栏 + 主区 + 悬浮输入卡）。

结构参考主流桌面 Agent：左边导航与项目，右边对话，底部一块悬浮输入卡。
线程模型不变：Agent 跑后台线程，事件经 queue 回主线程 —— Tk 不允许跨线程操作。
"""

from __future__ import annotations

import os
import queue
import re
import sys
import threading
import time

try:
    import tkinter as tk
    import tkinter.font as tkfont
    from tkinter import filedialog, messagebox
except ModuleNotFoundError:  # 解释器没带 tkinter
    try:
        import ctypes

        ctypes.windll.user32.MessageBoxW(
            0,
            "当前 Python 没有 tkinter，无法打开图形界面。\n"
            "请用带 tkinter 的解释器启动，例如：\nC:\\Python314\\pythonw.exe gui.py",
            APP_NAME,
            0x10,
        )
    except Exception:
        pass
    raise SystemExit(1)

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

from ..config import (  # noqa: E402
    APP_NAME,
    AVATAR_PATH,
    BASE_DIR,
    ICON_PATH,
    ICON_SOURCE,
    MODE_LABELS,
    MODES,
    ConfigError,
    default_mode,
    load_env,
)
from ..core import Agent, Policy, Session, SessionStore, Sink, refresh_tools  # noqa: E402
from ..core.loop import INTERNAL_PREFIXES  # noqa: E402
from ..core.session import MAX_INTERJECTS  # noqa: E402
from ..model import LLM, by_provider, mainstream_specs, providers  # noqa: E402
from ..tools import DANGEROUS, checkpoints  # noqa: E402
from . import theme  # noqa: E402
from .history_dialog import HistoryDialog  # noqa: E402
from .settings import UISettings, load as load_settings, save as save_settings  # noqa: E402
from .widgets import (  # noqa: E402
    Avatar,
    IconButton,
    round_photo,
    CircleButton,
    Divider,
    NavItem,
    Pill,
    Toggle,
    Tooltip,
)

MD_INLINE = re.compile(r"(\*\*.+?\*\*|`[^`]+`)")


class GuiSink(Sink):
    """把 Agent 事件塞进队列，交给主线程渲染。"""

    def __init__(self, q: queue.Queue) -> None:
        self.q = q

    def text(self, piece: str) -> None:
        self.q.put(("text", piece))

    def text_done(self) -> None:
        self.q.put(("text_done", None))

    def tool_call(self, name: str, args: str) -> None:
        self.q.put(("tool_call", (name, args)))

    def tool_result(self, result: str) -> None:
        self.q.put(("tool_result", result))

    def step_done(self, step: int) -> None:
        self.q.put(("step_done", step))

    def state(self, snapshot: dict) -> None:
        self.q.put(("state", snapshot))

    def notice(self, message: str) -> None:
        self.q.put(("notice", message))

    def plan(self, steps: list[dict], replanned: bool = False) -> None:
        self.q.put(("plan", (steps, replanned)))

    def plan_step(self, index: int, status: str, note: str = "") -> None:
        self.q.put(("plan_step", (index, status, note)))

    def error(self, message: str) -> None:
        self.q.put(("error", message))

    def confirm(self, name: str, args: str, hint: str = "") -> bool:
        """后台线程不能碰 Tk：弹窗交给主线程，这里阻塞等结果。"""
        done = threading.Event()
        box = {"ok": False}
        self.q.put(("confirm", (name, args, hint, done, box)))
        done.wait()
        return box["ok"]


class ChatWindow:
    PLACEHOLDER = "请随意输入"

    # 侧栏宽度按窗口宽度的比例算，夹在这个区间里。
    # 15%~20% 是既能放下「搜索对话、任务、产物」又不抢主区的范围。
    SIDEBAR_RATIO = 0.18
    SIDEBAR_MIN = 268
    SIDEBAR_MAX = 420
    SIDEBAR_W = 290  # 建完窗口后会被实际值覆盖，这里只是初值

    def __init__(self, root: tk.Tk, llm, session: Session | None = None,
                 prefs: UISettings | None = None) -> None:
        self.root = root
        self.llm = llm
        self.session = session or Session()
        self.store = SessionStore()  # 会话存档：关窗不丢，可从「历史会话」恢复
        self.store.attach(self.session)
        self.prefs = prefs or UISettings()
        self.queue: queue.Queue = queue.Queue()
        self.sink = GuiSink(self.queue)
        self.cancel: threading.Event | None = None
        self.running = False
        self._buf = ""
        self._hero = True
        self._placeholder_active = False
        self._recent: list[tuple[str, str]] = []  # (文本, Text 位置)
        self.task_state: dict | None = None
        self.policy = Policy.default()  # 会话级边界；用户主动附加的文件会临时加白
        self.mode = default_mode()  # react / plan，可在界面上切换
        self.sidebar_visible = True
        self._resize_job = None

        self._build_ui()
        root.after(40, self._pump)
        root.protocol("WM_DELETE_WINDOW", self._on_close)

    def _build_ui(self) -> None:
        """把整个界面搭起来。换主题时销毁重来，所以单独抽出来。"""
        root = self.root
        root.title(APP_NAME)
        apply_geometry(root, self.prefs.font_scale)
        root.configure(bg=theme.BG)

        self._build_sidebar()
        self._build_main()
        self._show_hero()
        self._show_placeholder()
        self._refresh_recent()
        self._apply_sidebar_width()
        root.bind("<Configure>", self._on_root_resize, add="+")

    # ------------------------------------------------------------ 侧栏

    def _build_sidebar(self) -> None:
        bar = self.sidebar = tk.Frame(self.root, bg=theme.SIDEBAR, width=self.SIDEBAR_W)
        bar.pack(side="left", fill="y")
        bar.pack_propagate(False)

        # 顶部第一行是搜索。品牌挪到它下面 —— 这样进窗口第一眼就能搜。
        search_wrap = tk.Frame(bar, bg=theme.SIDEBAR)
        search_wrap.pack(fill="x", padx=12, pady=(12, 0))

        self.search_btn = Pill(
            search_wrap,
            text="🔍  搜索对话、任务、产物",
            command=self._open_search,
            variant="secondary",
            width=self.SIDEBAR_W - 24,
            height=32,
            radius=10,
            fg=theme.TEXT_MUTED,
            bg=theme.SIDEBAR,
        )
        self.search_btn.pack(fill="x")

        self._build_brand(bar)

        nav = tk.Frame(bar, bg=theme.SIDEBAR)
        nav.pack(fill="x", padx=8, pady=(2, 8))
        NavItem(nav, "＋", "新聊天", self._new_session).pack(fill="x")
        NavItem(nav, "🕘", "历史会话", self._open_history).pack(fill="x")
        NavItem(nav, "🧩", "插件", self._open_plugins).pack(fill="x")

        Divider(bar).pack(fill="x", padx=14, pady=6)

        tk.Label(bar, text="项目", bg=theme.SIDEBAR, fg=theme.TEXT_FAINT, font=theme.FONT_SM, anchor="w").pack(
            fill="x", padx=22, pady=(2, 4)
        )
        project = tk.Frame(bar, bg=theme.SIDEBAR)
        project.pack(fill="x", padx=8)
        self._project_row = tk.Frame(project, bg=theme.SIDEBAR_ACTIVE)
        self._project_row.pack(fill="x")
        tk.Label(
            self._project_row, text="🗀", bg=theme.SIDEBAR_ACTIVE, fg=theme.TEXT_MUTED, font=theme.FONT_SM
        ).pack(side="left", padx=(8, 4), pady=6)
        tk.Label(
            self._project_row,
            text=BASE_DIR.name,
            bg=theme.SIDEBAR_ACTIVE,
            fg=theme.TEXT,
            font=theme.FONT_SM,
            anchor="w",
        ).pack(side="left", pady=6)
        # 路径只显示最后两段。写全路径会折成两行，侧栏看着最挤的就是这儿。
        tail = os.sep.join(BASE_DIR.parts[-2:]) if len(BASE_DIR.parts) >= 2 else str(BASE_DIR)
        path_label = tk.Label(
            bar,
            text=f"…{os.sep}{tail}",
            bg=theme.SIDEBAR,
            fg=theme.TEXT_GHOST,
            font=theme.FONT_SM,
            anchor="w",
            cursor="hand2",
        )
        path_label.pack(fill="x", padx=22, pady=(4, 10))
        Tooltip(path_label, str(BASE_DIR), delay=250, wraplength=420)

        tk.Label(bar, text="最近", bg=theme.SIDEBAR, fg=theme.TEXT_FAINT, font=theme.FONT_SM, anchor="w").pack(
            fill="x", padx=22, pady=(2, 2)
        )
        self.recent_frame = tk.Frame(bar, bg=theme.SIDEBAR)
        self.recent_frame.pack(fill="both", expand=True, padx=8, pady=(0, 8))

        # 底部是用户区。模型切换挪进设置后这里空出来了，正好留给账号入口。
        bottom = tk.Frame(bar, bg=theme.SIDEBAR)
        bottom.pack(fill="x", side="bottom", padx=12, pady=10)

        self.user_row = tk.Frame(bottom, bg=theme.SIDEBAR, cursor="hand2")
        self.user_row.pack(fill="x")
        self.avatar = Avatar(
            self.user_row,
            size=42,
            image_path=AVATAR_PATH if AVATAR_PATH.is_file() else None,
            on_click=self._open_user_menu,
            bg=theme.SIDEBAR,
        )
        self.avatar.pack(side="left")

        self.user_name = tk.Label(
            self.user_row, text="游客", bg=theme.SIDEBAR, fg=theme.TEXT, font=theme.FONT_BODY, anchor="w"
        )
        self.user_name.pack(side="left", padx=(9, 0))

        for widget in (self.user_row, self.user_name):
            widget.bind("<Button-1>", lambda e: self._open_user_menu(self.user_row))

        self._divider = tk.Frame(self.root, bg=theme.BORDER, width=1)
        self._divider.pack(side="left", fill="y")

    def _build_brand(self, parent) -> None:
        """品牌行：大图标 + 墟 + 折叠按钮。

        标题栏里那个图标只有 16px、字只有 9pt，看不清。这里重画一份，
        图标按 DPI 放到 56px 左右、字放到 26pt，大约是标题栏的 3~4 倍。
        """
        row = tk.Frame(parent, bg=theme.SIDEBAR)
        row.pack(fill="x", padx=14, pady=(14, 6))

        size = max(40, round(56 * max(1.0, theme.DPI_SCALE)))
        self.brand_mark_photo = None
        if ICON_SOURCE.is_file():
            self.brand_mark_photo = round_photo(ICON_SOURCE, size, radius=size * 0.26)
        if self.brand_mark_photo is not None:
            mark = tk.Label(row, image=self.brand_mark_photo, bg=theme.SIDEBAR, bd=0)
            mark.pack(side="left")
        else:
            mark = tk.Label(
                row, text="墟", bg=theme.ACCENT, fg=theme.ON_ACCENT,
                font=theme.FONT_HERO, width=2, height=1,
            )
            mark.pack(side="left")

        hero_size = min(34, max(24, round(theme.FONT_HERO[1] * 1.3)))
        self.brand_label = tk.Label(
            row,
            text=APP_NAME,
            bg=theme.SIDEBAR,
            fg=theme.TEXT,
            font=(theme.FONT_HERO[0], hero_size, "bold"),
            anchor="w",
        )
        self.brand_label.pack(side="left", padx=(12, 0))

        self.collapse_btn = IconButton(
            row,
            command=self._toggle_sidebar,
            size=28,
            fg=theme.TEXT_MUTED,
            bg=theme.SIDEBAR,
        )
        self.collapse_btn.pack(side="right")
        Tooltip(self.collapse_btn, "收起侧边栏（Ctrl+B）", delay=300)
        self.root.bind("<Control-b>", lambda e: self._toggle_sidebar())

    # ------------------------------------------------------------ 侧栏宽度 / 折叠

    def _sidebar_px(self) -> int:
        width = self.root.winfo_width()
        if width <= 1:  # 还没 layout，先用窗口设定值
            width = 1400
        return max(self.SIDEBAR_MIN, min(self.SIDEBAR_MAX, round(width * self.SIDEBAR_RATIO)))

    def _apply_sidebar_width(self) -> None:
        self.SIDEBAR_W = self._sidebar_px()
        self.sidebar.configure(width=self.SIDEBAR_W)
        self.search_btn.set_size(self.SIDEBAR_W - 24)

    def _on_root_resize(self, event) -> None:
        """窗口尺寸变了按比例重算侧栏宽度。防抖：拖动时别每像素重画一次。"""
        if event.widget is not self.root or not self.sidebar_visible:
            return
        if self._resize_job is not None:
            try:
                self.root.after_cancel(self._resize_job)
            except tk.TclError:
                pass
        self._resize_job = self.root.after(80, self._resize_now)

    def _resize_now(self) -> None:
        self._resize_job = None
        if self.sidebar.winfo_exists() and self.sidebar_visible:
            self._apply_sidebar_width()

    def _toggle_sidebar(self, show: bool | None = None) -> None:
        """收起 / 展开侧边栏。收起时在主区左上角留一个把手。"""
        show = (not self.sidebar_visible) if show is None else show
        if show == self.sidebar_visible:
            return

        self.sidebar_visible = show
        if show:
            # 先放分隔线再放侧栏：pack 的 before 要求参照控件已经 packed
            self._divider.pack(side="left", fill="y", before=self.main)
            self.sidebar.pack(side="left", fill="y", before=self._divider)
            self.topbar.pack_forget()
            self._apply_sidebar_width()
        else:
            self.sidebar.pack_forget()
            self._divider.pack_forget()
            self.topbar.pack(fill="x", pady=(8, 0), before=self.chat_wrap)
        self._set_status(
            "侧边栏已收起（Ctrl+B 展开）" if not show else "就绪",
            theme.TEXT_MUTED,
            theme.SUCCESS,
        )

    def _refresh_recent(self) -> None:
        for child in self.recent_frame.winfo_children():
            child.destroy()

        if not self._recent:
            tk.Label(
                self.recent_frame,
                text="还没有对话",
                bg=theme.SIDEBAR,
                fg=theme.TEXT_GHOST,
                font=theme.FONT_SM,
                anchor="w",
            ).pack(fill="x", padx=14, pady=4)
            return

        for text, index in self._recent[-8:][::-1]:
            short = text if len(text) <= 22 else text[:22] + "…"
            row = tk.Label(
                self.recent_frame,
                text="· " + short,
                bg=theme.SIDEBAR,
                fg=theme.TEXT_MUTED,
                font=theme.FONT_SM,
                anchor="w",
                cursor="hand2",
            )
            row.pack(fill="x", padx=14, pady=2)
            row.bind("<Button-1>", lambda e, i=index: self._jump_to(i))
            row.bind("<Enter>", lambda e, w=row: w.configure(fg=theme.TEXT))
            row.bind("<Leave>", lambda e, w=row: w.configure(fg=theme.TEXT_MUTED))

    def _jump_to(self, index: str) -> None:
        try:
            self.chat.see(index)
        except tk.TclError:
            pass

    # ------------------------------------------------------------ 主区

    def _build_main(self) -> None:
        main = self.main = tk.Frame(self.root, bg=theme.BG)
        main.pack(side="left", fill="both", expand=True)

        # 先 pack 状态栏，它才会贴在窗口最底部，输入卡浮在它上面
        self._build_statusbar(main)
        self._build_composer(main)
        self._build_chat(main)
        self._build_topbar(main)

    def _build_topbar(self, parent) -> None:
        """侧栏收起时才出现的把手。平时不 pack，高度为零。"""
        bar = self.topbar = tk.Frame(parent, bg=theme.BG)
        self.expand_btn = IconButton(
            bar, command=self._toggle_sidebar, size=28, fg=theme.TEXT_MUTED, bg=theme.BG
        )
        self.expand_btn.pack(side="left", padx=(16, 10), pady=(4, 0))
        Pill(
            bar,
            text="🔍  搜索",
            command=self._open_search,
            variant="secondary",
            height=30,
            padx=12,
            bg=theme.BG,
        ).pack(side="left", pady=(4, 0))
        tk.Label(
            bar, text=APP_NAME, bg=theme.BG, fg=theme.TEXT_FAINT, font=theme.FONT_SM
        ).pack(side="left", padx=(10, 0), pady=(4, 0))

    def _build_chat(self, parent) -> None:
        wrap = self.chat_wrap = tk.Frame(parent, bg=theme.BG)
        wrap.pack(fill="both", expand=True)

        self.chat = tk.Text(
            wrap,
            bg=theme.BG,
            fg=theme.TEXT_BODY,
            font=theme.FONT_BODY,
            wrap="word",
            relief="flat",
            bd=0,
            padx=40,
            pady=14,
            insertwidth=0,
            spacing1=2,
            spacing3=2,
            state="disabled",
            cursor="arrow",
        )
        scroll = tk.Scrollbar(wrap, command=self.chat.yview, bd=0, width=9, troughcolor=theme.BG)
        self.chat.configure(yscrollcommand=scroll.set)
        scroll.pack(side="right", fill="y")
        self.chat.pack(side="left", fill="both", expand=True)

        c = self.chat
        c.tag_configure("hero_title", foreground=theme.TEXT, font=theme.FONT_HERO, justify="center", spacing1=6)
        c.tag_configure("hero_sub", foreground=theme.TEXT_FAINT, font=theme.FONT_BODY, justify="center", spacing1=12)
        c.tag_configure("hero_hint", foreground=theme.TEXT_GHOST, font=theme.FONT_SM, justify="center", spacing1=16)

        c.tag_configure("user", foreground=theme.ACCENT_DEEP, font=theme.FONT_BODY, justify="right",
                        lmargin1=160, rmargin=40, spacing1=10, spacing3=8)
        # 插话：右对齐但底色不同，一眼能看出这是运行中追加的，不是新的一轮
        c.tag_configure("interject", foreground=theme.ACCENT_DEEP, background=theme.ACCENT_SOFT,
                        font=theme.FONT_SM, justify="right", lmargin1=200, rmargin=40,
                        spacing1=6, spacing3=6)
        c.tag_configure("assistant", foreground=theme.TEXT_BODY, lmargin1=40, rmargin=120, spacing1=8)
        c.tag_configure("h", foreground=theme.TEXT, font=theme.FONT_H, lmargin1=40, spacing1=12, spacing3=3)
        c.tag_configure("bold", foreground=theme.TEXT, font=theme.FONT_BODY_BOLD)
        c.tag_configure("code", foreground=theme.ACCENT_DEEP, font=theme.FONT_MONO, background=theme.ACCENT_SOFT)
        c.tag_configure("trow", foreground=theme.TEXT_BODY, lmargin1=40, rmargin=120, spacing1=4, spacing3=1)
        c.tag_configure("trow_bold", foreground=theme.TEXT, font=theme.FONT_BODY_BOLD, lmargin1=40, spacing1=4)
        c.tag_configure("tool", foreground=theme.TEXT_FAINT, font=theme.FONT_MONO, lmargin1=52, spacing1=8)
        c.tag_configure("toolres", foreground=theme.TEXT_GHOST, font=theme.FONT_MONO, lmargin1=66, spacing1=2)
        c.tag_configure("step", foreground=theme.TEXT_GHOST, font=theme.FONT_SM, lmargin1=52, spacing3=8)
        c.tag_configure("notice", foreground=theme.NOTICE, font=theme.FONT_SM, lmargin1=40, spacing1=6)
        c.tag_configure("error", foreground=theme.DANGER, font=theme.FONT_BODY, lmargin1=40, spacing1=8, spacing3=8)

    def _build_statusbar(self, parent) -> None:
        row = tk.Frame(parent, bg=theme.BG)
        row.pack(fill="x", side="bottom", padx=40, pady=(0, 6))

        self.status_dot = tk.Canvas(row, width=8, height=8, bg=theme.BG, highlightthickness=0, bd=0)
        self._dot = self.status_dot.create_oval(1, 1, 7, 7, fill=theme.SUCCESS, outline="")
        self.status_dot.pack(side="left", pady=2)

        self.status = tk.Label(row, text="就绪", bg=theme.BG, fg=theme.TEXT_MUTED, font=theme.FONT_SM)
        self.status.pack(side="left", padx=(6, 0))

        # 状态观测：上下文占用与 token 用量（轮次/工具次数刻意不显示）
        self.meta = tk.Label(row, text="", bg=theme.BG, fg=theme.TEXT_GHOST, font=theme.FONT_SM)
        self.meta.pack(side="right")

    def _build_composer(self, parent) -> None:
        wrap = tk.Frame(parent, bg=theme.BG)
        wrap.pack(fill="x", side="bottom", padx=56, pady=(6, 2))

        chips = tk.Frame(wrap, bg=theme.BG)
        chips.pack(fill="x", pady=(0, 6))
        tk.Label(
            chips,
            text=f"🗀  {BASE_DIR.name}",
            bg=theme.BG_CARD,
            fg=theme.TEXT_MUTED,
            font=theme.FONT_SM,
            padx=10,
            pady=3,
        ).pack(side="left")

        self.safe_var = tk.BooleanVar(value=True)
        right = tk.Frame(chips, bg=theme.BG)
        right.pack(side="right")
        self.safe_toggle = Toggle(right, self.safe_var, bg=theme.BG)
        self.safe_toggle.pack(side="left", padx=(0, 6))
        self.safe_label = tk.Label(
            right, text="安全模式", bg=theme.BG, fg=theme.TEXT_MUTED, font=theme.FONT_SM, cursor="hand2"
        )
        self.safe_label.pack(side="left")
        # 开关和文字都能悬停出说明：这个开关关系到敢不敢让它动文件，得说清
        Tooltip(self.safe_toggle, self._safe_hint)
        Tooltip(self.safe_label, self._safe_hint)
        self.safe_toggle.bind("<Button-1>", lambda e: self.root.after(60, self._refresh_safe_hint_style), add="+")

        card = tk.Frame(wrap, bg=theme.BG, highlightbackground=theme.BORDER_STRONG, highlightthickness=1)
        card.pack(fill="x")

        self.input = tk.Text(
            card,
            height=3,
            bg=theme.BG,
            fg=theme.TEXT,
            font=theme.FONT_BODY,
            wrap="word",
            relief="flat",
            bd=0,
            padx=16,
            pady=12,
            insertbackground=theme.TEXT,
            highlightthickness=0,
        )
        self.input.pack(fill="x")
        self.input.bind("<Return>", self._on_return)
        self.input.bind("<Shift-Return>", lambda e: None)
        self.input.bind("<KeyPress>", self._before_key, add="+")

        row = tk.Frame(card, bg=theme.BG)
        row.pack(fill="x", padx=12, pady=(0, 10))

        Pill(
            row,
            text="📎  附件",
            command=self._attach_file,
            fill=theme.BG_SOFT,
            fill_hover=theme.BG_CARD,
            fg=theme.TEXT_MUTED,
            outline=theme.BORDER,
            height=28,
            radius=14,
            padx=12,
        ).pack(side="left", padx=(6, 0))

        Pill(
            row,
            text="＋  插件",
            command=self._open_plugins,
            fill=theme.BG_SOFT,
            fill_hover=theme.BG_CARD,
            fg=theme.TEXT_MUTED,
            outline=theme.BORDER,
            height=28,
            radius=14,
            padx=12,
        ).pack(side="left")

        self.undo_btn = Pill(
            row,
            text="↩  撤销写入",
            command=self._undo_write,
            fill=theme.BG_SOFT,
            fill_hover=theme.BG_CARD,
            fg=theme.TEXT_MUTED,
            outline=theme.BORDER,
            height=28,
            radius=14,
            padx=12,
        )
        self.undo_btn.pack(side="left", padx=(6, 0))
        Tooltip(self.undo_btn, "撤销 Agent 最近一次写文件（检查点保留最近 20 次）。", delay=400)

        self.send_btn = CircleButton(row, "↑", self.submit, size=32, fill=theme.INK, hover=theme.INK_HOVER)
        self.send_btn.pack(side="right", pady=6)

        # 模型名放在 ReAct 右边。pack(side="right") 是先放的更靠右，
        # 所以这里不能加太厚的 padding，否则和发送键挤在一起
        self.model_btn = tk.Label(
            row,
            text=f"{short_model_name(getattr(self.llm, 'label', ''))}  ⌄",
            bg=theme.BG,
            fg=theme.TEXT_MUTED,
            font=theme.FONT_SM,
            cursor="hand2",
        )
        self.model_btn.pack(side="right", padx=(0, 10), pady=6)
        self.model_btn.bind("<Button-1>", lambda e: self._open_model_menu(self.model_btn))
        Tooltip(self.model_btn, "切换模型。完整列表在「设置 → 模型」里。", delay=400)

        self.mode_btn = tk.Label(
            row,
            text=f"{MODE_LABELS.get(self.mode, self.mode)}  ⌄",
            bg=theme.BG,
            fg=theme.ACCENT_DEEP,
            font=theme.FONT_SM,
            cursor="hand2",
        )
        self.mode_btn.pack(side="right", padx=(0, 10), pady=6)
        self.mode_btn.bind("<Button-1>", lambda e: self._open_mode_menu(self.mode_btn))

        self.stop_btn = Pill(
            row,
            text="停止",
            command=self._stop,
            fill=theme.BG,
            fill_hover=theme.DANGER_SOFT,
            fg=theme.TEXT_GHOST,
            outline=theme.BORDER,
            height=28,
            radius=14,
            padx=12,
        )
        self.stop_btn.pack(side="right", padx=(0, 8), pady=6)
        self.stop_btn.set_enabled(False, fill=theme.BG, fg=theme.TEXT_GHOST)

    # ------------------------------------------------------------ 空状态与占位

    def _show_hero(self) -> None:
        self.chat.configure(state="normal")
        self.chat.delete("1.0", "end")
        self.chat.insert("end", "\n\n\n\n☁\n", "hero_hint")
        self.chat.insert("end", "我们该做点什么？\n", "hero_title")
        self.chat.insert("end", "我会自己查资料、跑命令、读写文件，查完直接给结论。\n", "hero_sub")
        self.chat.insert("end", "试试：「看看这个项目的结构」 · 「帮我算 17 的阶乘」\n", "hero_hint")
        self.chat.configure(state="disabled")
        self._hero = True

    def _clear_chat(self) -> None:
        self.chat.configure(state="normal")
        self.chat.delete("1.0", "end")
        self.chat.configure(state="disabled")
        self._hero = False

    def _show_placeholder(self) -> None:
        if self._placeholder_active or self.input.get("1.0", "end").strip():
            return
        self.input.insert("1.0", self.PLACEHOLDER)
        self.input.tag_add("ph", "1.0", "end")
        self.input.tag_configure("ph", foreground=theme.TEXT_GHOST)
        self._placeholder_active = True

    def _before_key(self, _event=None) -> None:
        if self._placeholder_active:
            self.input.delete("1.0", "end")
            self._placeholder_active = False

    # ------------------------------------------------------------ 渲染

    def _raw(self, text: str, tag: str) -> None:
        at_bottom = self.chat.yview()[1] > 0.995
        self.chat.configure(state="normal")
        self.chat.insert("end", text, tag)
        self.chat.configure(state="disabled")
        if at_bottom:
            self.chat.see("end")

    def _stream(self, piece: str) -> None:
        self._buf += piece
        while "\n" in self._buf:
            line, self._buf = self._buf.split("\n", 1)
            self._render_line(line)

    def _flush_stream(self) -> None:
        if self._buf:
            self._render_line(self._buf)
            self._buf = ""

    def _render_line(self, line: str) -> None:
        stripped = line.strip()
        if stripped:
            if stripped.startswith("#"):
                self._raw(stripped.lstrip("#").strip() + "\n", "h")
                return
            if stripped.startswith("|"):
                cells = [c.strip() for c in stripped.strip("|").split("|")]
                if all(not c or set(c) <= set("-: ") for c in cells):
                    return
                if len(cells) >= 2:
                    first = cells[0].strip("`*").strip()
                    rest = " · ".join(c.strip("`*").strip() for c in cells[1:] if c)
                    self._raw(first, "trow_bold")
                    self._raw("   ", "trow")
                    self._inline(rest, base="trow", bold="trow_bold", code="code")
                    self._raw("\n", "trow")
                    return
            if len(stripped) >= 3 and set(stripped) <= {"-", "*", "_"}:
                self._raw("─" * 44 + "\n", "step")
                return
            if stripped.startswith(("- ", "* ")):
                indent = " " * (len(line) - len(line.lstrip()))
                self._raw(indent + "• ", "assistant")
                self._inline(stripped[2:])
                self._raw("\n", "assistant")
                return

        self._inline(line)
        self._raw("\n", "assistant")

    def _inline(self, text: str, base: str = "assistant", bold: str = "bold", code: str = "code") -> None:
        for part in MD_INLINE.split(text):
            if not part:
                continue
            if part.startswith("**") and part.endswith("**") and len(part) > 4:
                self._raw(part[2:-2], bold)
            elif part.startswith("`") and part.endswith("`") and len(part) > 2:
                self._raw(part[1:-1], code)
            else:
                self._raw(part, base)

    def _render_plan(self, steps: list[dict], replanned: bool = False) -> None:
        self._raw("\n重新规划：\n" if replanned else "\n计划：\n", "h")
        for index, step in enumerate(steps, 1):
            self._raw(f"  {index}. {step.get('goal', '')}\n", "assistant")

    def _render_plan_step(self, index: int, status: str, note: str = "") -> None:
        marks = {"running": "…", "done": "✓", "failed": "✗"}
        tag = "error" if status == "failed" else "step"
        self._raw(f"  {marks.get(status, '·')} 第 {index} 步 {note}\n", tag)

    def _safe_hint(self) -> str:
        """安全模式的悬停说明。开关状态不同，文案跟着变。"""
        managed = "、".join(sorted(DANGEROUS)) or "（当前没有危险工具）"
        if self.safe_var.get():
            head = "当前：开启 —— 危险操作执行前会弹窗问你。"
        else:
            head = "当前：关闭 —— 危险操作会直接执行，不再询问。"
        return (
            f"{head}\n\n"
            f"受管操作：{managed}\n\n"
            "开启时：每次执行都先问，你点了拒绝，模型会换一种做法，不会硬闯。\n"
            "关闭时：直接跑，适合目标明确的任务。\n\n"
            "注意：这个开关只管「要不要问」。读写目录白名单、命令黑名单这些硬边界不受影响，始终生效。\n"
            "命令行可用 --safe 强制开启。"
        )

    def _refresh_safe_hint_style(self) -> None:
        """关掉安全模式时把文字变红，提醒现在是「不问了」的状态。"""
        self.safe_label.configure(fg=theme.TEXT_MUTED if self.safe_var.get() else theme.DANGER)

    def _set_status(self, text: str, color: str | None = None, dot: str | None = None) -> None:
        self.status.configure(text=text, fg=color or theme.TEXT_MUTED)
        self.status_dot.itemconfig(self._dot, fill=dot or theme.SUCCESS)

    def _render_state(self, snap: dict) -> None:
        """把内核的状态快照落到界面上：上下文、token、完成度。"""
        self.task_state = snap
        ratio = snap.get("context_ratio", 0.0)
        out_tokens = snap.get("output_tokens", 0)
        self.meta.configure(text=f"上下文 {ratio * 100:.0f}%  ·  输出 {out_tokens} tok")

        status = snap.get("status")
        phases = {
            "planning": "思考中…",
            "tooling": "执行中…",
            "checking": "检查完成度…",
        }
        if status in phases:
            completion = snap.get("completion", 0.0)
            self._set_status(f"{phases[status]} 完成度 {completion * 100:.0f}%", theme.ACCENT, theme.ACCENT)
        elif status == "done":
            self._set_status("就绪", theme.TEXT_MUTED, theme.SUCCESS)

    def _pump(self) -> None:
        # 窗口已经销毁时别再排下一轮，否则 Tcl 会报 invalid command name
        try:
            if not self.root.winfo_exists():
                return
        except tk.TclError:
            return
        try:
            while True:
                kind, payload = self.queue.get_nowait()

                if kind == "text":
                    self._stream(payload)
                elif kind == "text_done":
                    self._flush_stream()
                elif kind == "tool_call":
                    name, args = payload
                    preview = args if len(args) <= 200 else args[:200] + "…"
                    self._raw(f"\n▸ {name}  {preview}\n", "tool")
                    self._set_status(f"正在调用 {name}", theme.ACCENT, theme.ACCENT)
                elif kind == "tool_result":
                    lines = payload.splitlines()
                    preview = "\n".join(lines[:7])
                    if len(lines) > 7:
                        preview += f"\n… 共 {len(lines)} 行"
                    self._raw(preview + "\n", "toolres")
                elif kind == "step_done":
                    self._raw(f"— 第 {payload} 步完成 —\n", "step")
                elif kind == "state":
                    self._render_state(payload)
                elif kind == "notice":
                    self._raw(f"· {payload}\n", "notice")
                elif kind == "error":
                    self._raw(payload + "\n", "error")
                    self._set_status("出错了", theme.DANGER, theme.DANGER)
                elif kind == "confirm":
                    name, args, hint, done, box = payload
                    detail = f"\n{hint}" if hint else ""
                    box["ok"] = messagebox.askyesno(
                        "确认执行",
                        f"Agent 想执行：\n\n{name}\n{args[:500]}{detail}\n\n允许吗？",
                        parent=self.root,
                    )
                    done.set()
                elif kind == "done":
                    self._finish()
        except queue.Empty:
            pass

        self.root.after(40, self._pump)

    def _finish(self) -> None:
        self.running = False
        self.cancel = None
        self.send_btn.set_enabled(True, fill=theme.INK)
        self.stop_btn.set_enabled(False, fill=theme.BG, fg=theme.TEXT_GHOST)
        self._set_status("就绪", theme.TEXT_MUTED, theme.SUCCESS)
        self._show_placeholder()
        self.input.focus_set()

    # ------------------------------------------------------------ 交互

    @property
    def is_idle(self) -> bool:
        return not self.running

    def _on_return(self, event) -> str | None:
        if event.state & 0x0001:
            return None
        self.submit()
        return "break"

    def submit(self, text: str | None = None) -> None:
        if text is None:
            text = "" if self._placeholder_active else self.input.get("1.0", "end").strip()
        if not text:
            return

        # 正在跑就当作插话：不打断当前步骤，等内核下一个安全点取走
        if self.running:
            self.input.delete("1.0", "end")
            self._placeholder_active = False
            self._interject(text)
            return

        if self._hero:
            self._clear_chat()

        self.input.delete("1.0", "end")
        self._placeholder_active = False

        self.session.add_user(text)
        prefix = "" if not self.chat.get("1.0", "end").strip() else "\n"
        self._raw(f"{prefix}{text}\n", "user")
        self._recent.append((text, self.chat.index("end-1c")))
        self._refresh_recent()

        self.running = True
        self.cancel = threading.Event()
        # 发送键在运行中保持可用：点它就是插话
        self.send_btn.set_enabled(True, fill=theme.ACCENT_DEEP)
        self.stop_btn.set_enabled(True, fill=theme.BG, fg=theme.TEXT_MUTED)
        self._set_status("思考中…", theme.ACCENT, theme.ACCENT)

        threading.Thread(target=self._worker, args=(self.safe_var.get(), self.cancel), daemon=True).start()

    def _interject(self, text: str) -> None:
        """把用户的新要求排进队列，由内核在安全点转成消息。"""
        evicted = self.session.interject(text)
        prefix = "" if not self.chat.get("1.0", "end").strip() else "\n"
        self._raw(f"{prefix}↑ 插话  {text}\n", "interject")
        if evicted:
            self._raw(f"（插话队列已满 {MAX_INTERJECTS} 条，最早的一条被挤出）\n", "notice")
        self._recent.append((text, self.chat.index("end-1c")))
        self._refresh_recent()
        pending = self.session.pending_interjects
        self._set_status(
            f"已插话 {pending}/{MAX_INTERJECTS}，下一步生效", theme.ACCENT_DEEP, theme.ACCENT
        )

    def _worker(self, safe: bool, cancel: threading.Event) -> None:
        try:
            Agent(self.llm, policy=self.policy, mode=self.mode).run(
                self.session, sink=self.sink, safe=safe, cancel=cancel
            )
        except Exception as exc:  # noqa: BLE001 后台线程不能静默死掉
            self.queue.put(("error", f"未预期错误：{type(exc).__name__}: {exc}"))
        finally:
            self.queue.put(("done", None))

    def _stop(self) -> None:
        if self.cancel is not None:
            self.cancel.set()
            self._set_status("正在停止（当前步骤结束后生效）…", theme.DANGER, theme.DANGER)

    def _new_session(self) -> None:
        if self.running:
            messagebox.showinfo(APP_NAME, "当前任务还在跑，先点停止。", parent=self.root)
            return
        # 旧会话原样留在存档里，可从「历史会话」找回；这里开一份全新的
        self.session = Session()
        self.store = SessionStore()
        self.store.attach(self.session)
        self._recent.clear()
        self._refresh_recent()
        self._show_hero()
        self._show_placeholder()
        self._set_status("就绪（新会话已开始存档）", theme.TEXT_MUTED, theme.SUCCESS)

    def _undo_write(self) -> None:
        """撤销 Agent 最近一次写文件。结果打到聊天区，跟其他系统提示一个样式。"""
        if self.running:
            messagebox.showinfo(APP_NAME, "当前任务还在跑，等它收手再撤销。", parent=self.root)
            return
        result = checkpoints.undo_last()
        prefix = "" if not self.chat.get("1.0", "end").strip() else "\n"
        self._raw(f"{prefix}↩ {result}\n", "notice")
        self._set_status("就绪" if "已" not in result else result[:40], theme.TEXT_MUTED, theme.SUCCESS)

    def _open_history(self) -> None:
        if self.running:
            messagebox.showinfo(APP_NAME, "当前任务还在跑，先点停止。", parent=self.root)
            return
        HistoryDialog(self.root, on_restore=self._restore_session)

    def _restore_session(self, store: SessionStore) -> None:
        """从存档恢复一段对话：换上恢复出来的 Session，把问答重画进聊天区。"""
        if self.running:
            messagebox.showinfo(APP_NAME, "当前任务还在跑，先点停止。", parent=self.root)
            return
        try:
            self.session = store.load()
        except OSError as exc:
            messagebox.showerror(APP_NAME, f"恢复失败：{exc}", parent=self.root)
            return
        self.store = store  # 继续聊会写回同一个存档文件
        self._recent.clear()
        self._replay_history()
        self._set_status(f"已恢复：{store.title or store.session_id}", theme.TEXT_MUTED, theme.SUCCESS)

    # ------------------------------------------------------------ 模型切换

    def _menu_style(self, parent) -> dict:
        """所有菜单共用一套外观，跟着主题走。"""
        return {
            "tearoff": 0,
            "bg": theme.BG,
            "fg": theme.TEXT,
            "activebackground": theme.ACCENT,
            "activeforeground": theme.ON_ACCENT,
            "bd": 0,
            "relief": "flat",
            "font": theme.FONT_SM,
        }

    def _open_model_menu(self, anchor) -> None:
        if self.running:
            return
        menu = tk.Menu(self.root, **self._menu_style(self.root))
        current = getattr(self.llm, "label", "")

        def item(parent, spec) -> None:
            mark = "✓  " if spec.key == current else "    "
            parent.add_command(
                label=f"{mark}{spec.key}",
                command=lambda k=spec.key: self._switch_model(k),
            )

        # 首屏只放主流型号，其余进「更多模型」
        mainstream = mainstream_specs()
        shown = {spec.key for spec in mainstream}
        for spec in mainstream:
            item(menu, spec)

        menu.add_separator()
        more = tk.Menu(menu, **self._menu_style(menu))
        for provider in providers():
            rest = [s for s in by_provider(provider) if s.key not in shown]
            if not rest:
                continue
            sub = tk.Menu(more, **self._menu_style(more))
            for spec in rest:
                item(sub, spec)
            more.add_cascade(label=f"{provider}（{len(rest)}）", menu=sub)
        if more.index("end") is not None:
            menu.add_cascade(label="更多模型", menu=more)

        menu.add_command(label="＋  添加模型…", command=self._open_add_model)

        x = anchor.winfo_rootx()
        y = anchor.winfo_rooty() + anchor.winfo_height() + 4
        try:
            menu.tk_popup(x, y)
        finally:
            menu.grab_release()

    def _open_add_model(self) -> None:
        from .settings_dialog import ModelDialog

        def done(spec) -> None:
            self._switch_model(spec.key)
            self._set_status(f"已添加模型 {spec.key}，可直接使用", theme.ACCENT_DEEP, theme.ACCENT)

        ModelDialog(self.root, on_added=done)

    def _open_mode_menu(self, anchor) -> None:
        if self.running:
            return
        menu = tk.Menu(
            self.root,
            tearoff=0,
            bg=theme.BG,
            fg=theme.TEXT,
            activebackground=theme.ACCENT,
            activeforeground=theme.ON_ACCENT,
            bd=0,
            relief="flat",
            font=theme.FONT_SM,
        )
        labels = {
            "react": "ReAct（边做边看）",
            "plan": "计划执行（先规划再动手）",
        }
        for value in MODES:
            mark = "✓  " if value == self.mode else "    "
            menu.add_command(label=f"{mark}{labels.get(value, value)}", command=lambda v=value: self._switch_mode(v))
        x = anchor.winfo_rootx()
        y = anchor.winfo_rooty() + anchor.winfo_height() + 4
        try:
            menu.tk_popup(x, y)
        finally:
            menu.grab_release()

    def _switch_mode(self, mode: str) -> None:
        if self.running or mode not in MODES:
            return
        self.mode = mode
        self.mode_btn.configure(text=f"{MODE_LABELS.get(mode, mode)}  ⌄")
        self._set_status(f"工作模式：{MODE_LABELS.get(mode, mode)}", theme.TEXT_MUTED, theme.SUCCESS)

    def _switch_model(self, key: str) -> None:
        if self.running:
            return
        try:
            self.llm = LLM(model=key)
        except ConfigError as exc:
            messagebox.showerror(
                "切换失败", f"{exc}\n\n这个模型需要它自己的 Key，写进 .env 就能用。", parent=self.root
            )
            return
        self.model_btn.configure(text=f"{short_model_name(self.llm.label)}  ⌄")
        self._set_status(f"已切换到 {self.llm.label}", theme.TEXT_MUTED, theme.SUCCESS)

    # ------------------------------------------------------------ 插件

    def _open_plugins(self) -> None:
        from .dialogs import PluginDialog

        PluginDialog(self.root, on_change=self._on_plugins_changed)

    # ------------------------------------------------------------ 搜索 / 账号

    def _open_search(self) -> None:
        from .search_dialog import SearchDialog

        SearchDialog(self.root, self.session, on_jump=self._jump_to_text)

    def _jump_to_text(self, needle: str) -> None:
        """在聊天区找到这段文字并滚过去。找不到就什么都不做。"""
        if not needle:
            return
        try:
            index = self.chat.search(needle, "1.0", "end")
        except tk.TclError:
            return
        if index:
            self.chat.see(index)

    def _open_user_menu(self, anchor) -> None:
        """账号入口。用户系统还没接，先放说明和设置，不做成假的能点。"""
        menu = tk.Menu(self.root, **self._menu_style(self.root))
        menu.add_command(label="游客 · 未登录", state="disabled")
        menu.add_separator()
        menu.add_command(label="⚙   设置…", command=self._open_settings)
        menu.add_separator()
        # 带上当前模型：模型入口收进来之后，这里是唯一能一眼看到的地方
        menu.add_command(
            label=f"◇   模型 · {short_model_name(getattr(self.llm, 'label', ''))}",
            command=lambda: self._open_model_menu(self.user_row),
        )

        x = anchor.winfo_rootx()
        y = anchor.winfo_rooty() - 6
        try:
            menu.tk_popup(x, y)
        finally:
            menu.grab_release()

    # ------------------------------------------------------------ 设置

    def _open_settings(self) -> None:
        from .settings_dialog import SettingsDialog

        SettingsDialog(
            self.root,
            self.prefs,
            on_apply=self._apply_prefs,
            on_plugins=self._open_plugins,
            on_model=self._switch_model,
            on_add_model=self._open_add_model,
            current_model=getattr(self.llm, "label", ""),
        )

    def _apply_prefs(self, prefs: UISettings) -> None:
        self.prefs = prefs
        save_settings(prefs)
        self._reload_ui()

    def _reload_ui(self) -> None:
        """换主题或字体后重建整个界面。

        Tk 控件在构造时就把颜色和字体写死了，改模块变量不会回写到已经存在的控件，
        所以只能全拆了重搭。对话内容按历史重放一遍。
        """
        if self.running:
            return
        for child in self.root.winfo_children():
            child.destroy()

        theme.apply_theme(self.prefs.theme)
        detect_fonts(self.root, self.prefs)

        self._buf = ""
        self._hero = True
        self._placeholder_active = False
        self._recent.clear()
        self._build_ui()
        self._replay_history()

    def _replay_history(self) -> None:
        """重建界面 / 恢复存档后把问答重画一遍。工具轨迹不重放，太长也不必要。

        只画两类消息：没被内核改写的用户输入、不带工具调用的模型答复。
        中间的执行过程（工具调用、计划、插话）属于过程性内容，画出来反而吵。
        """
        qa: list[tuple[str, str]] = []
        for msg in self.session.messages:
            role = msg.get("role")
            text = str(msg.get("content") or "").strip()
            if not text:
                continue
            if role == "user" and not text.startswith(INTERNAL_PREFIXES):
                qa.append(("user", text))
            elif role == "assistant" and not msg.get("tool_calls"):
                qa.append(("assistant", text))

        if not qa:
            return

        self._clear_chat()
        for index, (kind, text) in enumerate(qa):
            prefix = "" if index == 0 else "\n"
            if kind == "user":
                self._raw(f"{prefix}{text}\n", "user")
                self._recent.append((text, self.chat.index("end-1c")))
            else:
                for line in text.splitlines():
                    self._render_line(line)
                self._raw("\n", "assistant")
        self._refresh_recent()

    def _attach_file(self) -> None:
        """选文件 → 临时加入读白名单 → 把路径写进输入框。"""
        path = filedialog.askopenfilename(
            parent=self.root,
            title="选择要读的文件",
            filetypes=[
                ("文档", "*.docx *.xlsx *.pdf *.md *.txt *.csv *.json"),
                ("图片", "*.png *.jpg *.jpeg"),
                ("全部", "*.*"),
            ],
        )
        if not path:
            return

        self.policy.allow_read(path)
        if self._placeholder_active:
            self.input.delete("1.0", "end")
            self._placeholder_active = False

        current = self.input.get("1.0", "end").strip()
        prefix = "\n" if current else ""
        self.input.insert("end", f"{prefix}读一下这个文件：{path}\n")
        self.input.focus_set()
        self._set_status(f"已附加 {os.path.basename(path)}", theme.TEXT_MUTED, theme.SUCCESS)

    def _on_plugins_changed(self, tools: list[str] | None = None) -> None:
        self._on_extensions_changed()
        if tools is None:
            self._set_status("MCP 配置已更新，工具表已重建", theme.TEXT_MUTED, theme.SUCCESS)
            return
        self._set_status(
            f"插件已更新，插件工具 {len(tools)} 个" if tools else "插件已更新",
            theme.TEXT_MUTED,
            theme.SUCCESS,
        )

    def _on_extensions_changed(self) -> None:
        """插件或 MCP 变了：重建工具表，并刷新系统提示里的 MCP 摘要。"""
        refresh_tools()
        self.session.refresh_system()

    def _on_close(self) -> None:
        if self.cancel is not None:
            self.cancel.set()
        try:  # 别把 MCP 子进程留成孤儿
            from ..mcp.manager import mcp_manager

            mcp_manager.close_all()
        except Exception:  # noqa: BLE001
            pass
        self.root.destroy()

    # ------------------------------------------------------------ 测试辅助

    def wait_until_idle(self, timeout: float = 60) -> bool:
        deadline = time.time() + timeout
        while time.time() < deadline:
            self.root.update()
            if not self.running:
                self.root.update()
                return True
            time.sleep(0.01)
        return False


def short_model_name(key: str, limit: int = 18) -> str:
    """把模型名压短，给菜单这种窄地方用。

    按显示宽度算而不是字符数：中文占两格，不然「一个很长的中文模型名」
    按字符数没超、显示出来却撑满整行。
    """
    key = (key or "").strip()

    def width(text: str) -> int:
        return sum(2 if ord(ch) > 0x2E80 else 1 for ch in text)

    if width(key) <= limit:
        return key

    out = ""
    for ch in key:
        if width(out + ch) > limit - 1:
            break
        out += ch
    return out + "…"


def apply_icon(root: tk.Tk) -> None:
    """窗口和任务栏图标。用 default= 让后续弹出的 Toplevel 也继承。"""
    if not ICON_PATH.is_file():
        return
    try:
        root.iconbitmap(default=str(ICON_PATH))
    except tk.TclError:
        pass

def apply_geometry(root: tk.Tk, font_scale: float = 1.0) -> None:
    """窗口大小跟着 DPI 和用户字号走，不写死。

    声明 DPI 感知后，Tk 的几何单位是物理像素：150% 缩放的屏幕上字号会自动
    放大 1.5 倍，写死的 1160px 就装不下。用户把字号调到 130%，同理也要给版面留地方。

    字号的影响打个折（乘以 0.6），不然 160% 字号会让窗口直接占满屏 —— 字大了
    应该多出几行，而不是把窗口顶到屏幕边。
    """
    try:
        dpi_factor = root.winfo_fpixels("1i") / 96.0
    except tk.TclError:
        dpi_factor = 1.0

    scale = dpi_factor * (1 + (max(0.85, min(1.6, font_scale)) - 1) * 0.6)
    screen_w = root.winfo_screenwidth()
    screen_h = root.winfo_screenheight()

    width = min(int(1160 * scale), int(screen_w * 0.94))
    height = min(int(800 * scale), int(screen_h * 0.92))
    root.geometry(f"{max(760, width)}x{max(520, height)}")

    # 最小尺寸同样不能超过屏幕，否则小屏上窗口缩不小
    root.minsize(
        min(max(760, int(900 * scale)), int(screen_w * 0.8)),
        min(max(520, int(600 * scale)), int(screen_h * 0.7)),
    )


def apply_ui_preferences() -> UISettings:
    """读取设置并灌进 theme。必须在建 Tk 根窗口前调 DPI，在建设控件前调字体。"""
    prefs = load_settings()
    theme.apply_theme(prefs.theme)
    theme.apply_fonts(family=prefs.font_family or None, scale=prefs.font_scale)
    return prefs


def detect_fonts(root: tk.Tk, prefs: UISettings) -> None:
    """Tk 起来之后才知道系统有哪些字体；没指定就挑一个可用的，再算一次字号。"""
    try:
        available = set(tkfont.families(root))
    except tk.TclError:
        available = set()
    family = prefs.font_family or theme.pick_family(available, theme.SANS_CANDIDATES)
    mono = theme.pick_family(available, theme.MONO_CANDIDATES)
    theme.apply_fonts(family=family, mono=mono, scale=prefs.font_scale)
    # 声明了 DPI 感知后要自己把 point 换算过去，否则高分屏上字会偏小
    try:
        dpi = root.winfo_fpixels("1i")
        root.tk.call("tk", "scaling", dpi / 72.0)
        theme.set_dpi_scale(dpi / 96.0)
    except tk.TclError:
        pass


def create_app(
    llm=None, session: Session | None = None, prefs: UISettings | None = None
) -> tuple[tk.Tk, ChatWindow]:
    theme.enable_dpi_awareness()  # 必须在 Tk() 之前
    if prefs is None:
        prefs = apply_ui_preferences()
    else:  # 测试和重建时直接用传入的偏好，不读盘
        theme.apply_theme(prefs.theme)
        theme.apply_fonts(scale=prefs.font_scale)

    root = tk.Tk()
    apply_icon(root)
    detect_fonts(root, prefs)
    app = ChatWindow(root, llm or LLM(), session=session, prefs=prefs)
    return root, app


def main() -> int:
    os.chdir(BASE_DIR)
    load_env()
    try:
        llm = LLM()
    except ConfigError as exc:
        top = tk.Tk()
        top.withdraw()
        messagebox.showerror("配置缺失", str(exc), parent=top)
        top.destroy()
        return 2

    refresh_tools()  # 预热插件：坏的插件不影响启动
    root, _ = create_app(llm=llm)
    root.mainloop()
    return 0
