"""交互层：MCP 服务器管理。

三件事：加一个 server、同步工具清单、启用/停用。
和插件窗口的区别就在这句提示里 —— 插件是本地 Python 代码，
MCP 是**从外部拉起的独立进程**，所以装之前得让人看清启动的是什么命令。
"""

from __future__ import annotations

import queue
import threading
import tkinter as tk

from ..core import refresh_tools
from ..mcp.manager import ServerConfig, mcp_manager, save_config
from . import theme
from .widgets import Divider, Pill, ScrollFrame, Toggle

HINT_COMMAND = "例如  npx -y @modelcontextprotocol/server-filesystem D:\\资料"


class MCPDialog(tk.Toplevel):
    def __init__(self, master: tk.Misc, on_change=None) -> None:
        super().__init__(master, bg=theme.BG)
        self.on_change = on_change
        self.q: queue.Queue = queue.Queue()

        self.title("MCP 服务器")
        self.geometry(theme.dialog_geometry(self, 780, 640))
        self.minsize(theme.scale_px(640), theme.scale_px(520))
        self.transient(master)

        self._build_header()
        self._build_form()
        # 先占住底部：滚动区是 expand 的，后 pack 的会被挤成 1px 高
        self._build_footer()
        self._build_list()
        self.refresh_list()
        self.after(120, self._pump)

    # ------------------------------------------------------------ 结构

    def _build_header(self) -> None:
        head = tk.Frame(self, bg=theme.BG)
        head.pack(fill="x", padx=20, pady=(18, 4))
        tk.Label(head, text="MCP 服务器", bg=theme.BG, fg=theme.TEXT, font=theme.FONT_BRAND).pack(anchor="w")
        tk.Label(
            head,
            text=(
                "MCP 是外部工具进程（Model Context Protocol）。填启动命令即可接入，"
                "工具名会带上 mcp__ 前缀。命令是你自己机器上跑的程序，只加你信任的。"
            ),
            bg=theme.BG,
            fg=theme.TEXT_FAINT,
            font=theme.FONT_SM,
            justify="left",
            wraplength=700,
        ).pack(anchor="w", pady=(4, 0))

    def _build_form(self) -> None:
        box = tk.Frame(self, bg=theme.BG)
        box.pack(fill="x", padx=20, pady=(10, 6))
        box.columnconfigure(1, weight=1)

        self.vars = {}
        rows = (
            ("name", "名字", "短名，工具会变成 mcp__名字__工具，例如 fs"),
            ("command", "启动命令", HINT_COMMAND),
            ("tools", "工具白名单", "逗号分隔；留空 = 该 server 的全部工具"),
        )
        line = 0
        for key, label, hint in rows:
            tk.Label(box, text=label, bg=theme.BG, fg=theme.TEXT_MUTED, font=theme.FONT_SM).grid(
                row=line, column=0, sticky="w"
            )
            var = tk.StringVar()
            self.vars[key] = var
            entry = tk.Entry(
                box,
                textvariable=var,
                bg=theme.BG_SOFT,
                fg=theme.TEXT,
                insertbackground=theme.TEXT,
                relief="flat",
                font=theme.FONT_BODY,
                highlightthickness=1,
                highlightbackground=theme.BORDER_STRONG,
                highlightcolor=theme.ACCENT,
            )
            entry.grid(row=line, column=1, sticky="ew", padx=(12, 0), ipady=5)
            entry.bind("<Return>", lambda e: self._add())
            tk.Label(
                box,
                text=hint,
                bg=theme.BG,
                fg=theme.TEXT_GHOST,
                font=theme.FONT_SM,
                anchor="w",
                justify="left",
                wraplength=theme.scale_px(520),
            ).grid(row=line + 1, column=1, sticky="w", padx=(12, 0), pady=(0, 6))
            line += 2

        actions = tk.Frame(box, bg=theme.BG)
        actions.grid(row=line, column=1, sticky="w", padx=(12, 0), pady=(2, 0))
        Pill(actions, text="＋ 添加", command=self._add, variant="primary", bg=theme.BG, height=30).pack(
            side="left"
        )
        self.status = tk.Label(
            actions, text="", bg=theme.BG, fg=theme.TEXT_MUTED, font=theme.FONT_SM
        )
        self.status.pack(side="left", padx=(10, 0))

    def _build_list(self) -> None:
        Divider(self).pack(fill="x", padx=20, pady=(10, 0))
        self.summary = tk.Label(
            self, text="", bg=theme.BG, fg=theme.TEXT_MUTED, font=theme.FONT_SM, anchor="w"
        )
        self.summary.pack(fill="x", padx=22, pady=(8, 4))

        self.scroll = ScrollFrame(self, bg=theme.BG)
        self.scroll.pack(fill="both", expand=True, padx=12)

    def _build_footer(self) -> None:
        foot = tk.Frame(self, bg=theme.BG)
        foot.pack(side="bottom", fill="x", padx=20, pady=12)
        Pill(foot, text="关闭", command=self.destroy, variant="secondary", bg=theme.BG).pack(side="right")

    # ------------------------------------------------------------ 列表

    def refresh_list(self) -> None:
        for child in self.scroll.body.winfo_children():
            child.destroy()

        rows = mcp_manager.status()
        usage = mcp_manager.usage()

        if not rows:
            tk.Label(
                self.scroll.body,
                text="还没有配置 MCP 服务器。上面的表单填三个格就能加。",
                bg=theme.BG,
                fg=theme.TEXT_GHOST,
                font=theme.FONT_SM,
                anchor="w",
            ).pack(fill="x", padx=12, pady=10)
        for row in rows:
            self._row(self.scroll.body, row)

        self.summary.configure(
            text=(
                f"{len(rows)} 个 server · 注入 {usage['injected']} 个工具 schema"
                f"（{usage['tokens']} tok）+ 降级 {usage['dropped']} 个"
                f"（摘要 {usage['summary_tokens']} tok）· 预算 {usage['budget']} tok"
            )
        )

    def _row(self, parent, row: dict) -> None:
        card = tk.Frame(parent, bg=theme.BG, highlightbackground=theme.BORDER, highlightthickness=1)
        card.pack(fill="x", padx=8, pady=4)

        top = tk.Frame(card, bg=theme.BG)
        top.pack(fill="x", padx=12, pady=(8, 2))

        name = tk.Label(top, text=row["name"], bg=theme.BG, fg=theme.TEXT, font=theme.FONT_BODY_BOLD)
        name.pack(side="left")

        state = "已连接" if row["connected"] else ("未同步" if not row["error"] else "出错")
        state_color = theme.SUCCESS if row["connected"] else theme.TEXT_FAINT
        if row["error"]:
            state_color = theme.DANGER
        tk.Label(top, text=state, bg=theme.BG, fg=state_color, font=theme.FONT_SM).pack(
            side="left", padx=(8, 0)
        )
        tk.Label(
            top,
            text=f"· {row['tools']} 个工具 · 占 {row['tokens']} tok",
            bg=theme.BG,
            fg=theme.TEXT_GHOST,
            font=theme.FONT_SM,
        ).pack(side="left", padx=(6, 0))

        Pill(
            top,
            text="删除",
            command=lambda n=row["name"]: self._remove(n),
            variant="danger",
            bg=theme.BG,
            height=24,
            padx=10,
        ).pack(side="right")
        Pill(
            top,
            text="同步",
            command=lambda n=row["name"]: self._sync(n),
            variant="secondary",
            bg=theme.BG,
            height=24,
            padx=10,
        ).pack(side="right", padx=(0, 8))

        var = tk.BooleanVar(value=row["enabled"])
        Toggle(
            top, var, bg=theme.BG, command=lambda n=row["name"], v=var: self._toggle(n, v)
        ).pack(side="right", padx=(0, 10))

        body = tk.Frame(card, bg=theme.BG)
        body.pack(fill="x", padx=12, pady=(0, 8))
        tk.Label(
            body,
            text=row["command"][:110],
            bg=theme.BG,
            fg=theme.TEXT_FAINT,
            font=theme.FONT_MONO,
            anchor="w",
            justify="left",
            wraplength=640,
        ).pack(fill="x")
        if row["error"]:
            tk.Label(
                body,
                text=row["error"][:200],
                bg=theme.BG,
                fg=theme.DANGER,
                font=theme.FONT_SM,
                anchor="w",
                justify="left",
                wraplength=640,
            ).pack(fill="x", pady=(2, 0))

    # ------------------------------------------------------------ 操作

    def _add(self) -> None:
        name = self.vars["name"].get().strip()
        command = self.vars["command"].get().strip()
        if not name or not command:
            self._say("名字和启动命令都要填", theme.DANGER)
            return
        if any(s.name == name for s in mcp_manager.servers()):
            self._say(f"已经有一个叫 {name} 的了", theme.DANGER)
            return

        parts = command.split()
        whitelist = [t.strip() for t in self.vars["tools"].get().replace("，", ",").split(",") if t.strip()]
        servers = mcp_manager.servers()
        servers.append(
            ServerConfig(name=name, command=parts[0], args=parts[1:], tools=whitelist, enabled=True)
        )
        save_config(servers)
        mcp_manager.reload()
        for var in self.vars.values():
            var.set("")
        self._say(f"已添加 {name}，点「同步」拉工具清单", theme.SUCCESS)
        self.refresh_list()
        self._notify()

    def _remove(self, name: str) -> None:
        servers = [s for s in mcp_manager.servers() if s.name != name]
        save_config(servers)
        mcp_manager.reload()
        mcp_manager.close_all()
        self._say(f"已删除 {name}", theme.TEXT_MUTED)
        self.refresh_list()
        self._notify()

    def _toggle(self, name: str, var: tk.BooleanVar) -> None:
        servers = mcp_manager.servers()
        for server in servers:
            if server.name == name:
                server.enabled = bool(var.get())
        save_config(servers)
        mcp_manager.reload()
        self.refresh_list()
        self._notify()

    def _sync(self, name: str) -> None:
        """连接和 tools/list 可能要几百毫秒（npx 还要下载），扔后台别冻界面。"""
        self._say(f"正在连接 {name} …", theme.TEXT_MUTED)
        threading.Thread(
            target=lambda: self.q.put((name, mcp_manager.sync(name))), daemon=True
        ).start()

    def _pump(self) -> None:
        try:
            while True:
                name, message = self.q.get_nowait()
                ok = "个工具" in message
                self._say(message if ok else f"{name} 失败：{message}", theme.SUCCESS if ok else theme.DANGER)
                self.refresh_list()
                self._notify()
        except queue.Empty:
            pass
        if self.winfo_exists():
            self.after(150, self._pump)

    def _say(self, text: str, color: str) -> None:
        self.status.configure(text=text, fg=color)

    def _notify(self) -> None:
        refresh_tools()
        if self.on_change:
            self.on_change()
