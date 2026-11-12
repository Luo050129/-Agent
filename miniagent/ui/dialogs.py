"""交互层：插件管理窗口。

一个窗口干三件事：从 GitHub 装、启用/停用、卸载。
装完立刻 refresh 工具注册表，Agent 下一句话就能用，不用重启。
"""

from __future__ import annotations

import os
import queue
import threading
import tkinter as tk
from tkinter import messagebox

from .. import plugins, skills
from ..core import refresh_tools
from . import theme
from .widgets import Divider, Pill, Toggle, Tooltip


class PluginDialog(tk.Toplevel):
    def __init__(self, master: tk.Misc, on_change=None) -> None:
        super().__init__(master, bg=theme.BG)
        self.on_change = on_change
        self.q: queue.Queue = queue.Queue()

        self.title("插件")
        self.geometry(theme.dialog_geometry(self, 760, 580))
        self.minsize(theme.scale_px(640), theme.scale_px(460))
        self.transient(master)

        self._build_header()
        self._build_mcp_entry()
        self._build_installer()
        self._build_list()
        self.refresh_list()

    # ------------------------------------------------------------ 结构

    def _build_header(self) -> None:
        head = tk.Frame(self, bg=theme.BG)
        head.pack(fill="x", padx=20, pady=(18, 6))

        tk.Label(head, text="插件", bg=theme.BG, fg=theme.TEXT, font=theme.FONT_BRAND).pack(anchor="w")
        tk.Label(
            head,
            text="从 GitHub 装工具，装完 Agent 立刻可用。插件是第三方代码，只装你信任的仓库。",
            bg=theme.BG,
            fg=theme.TEXT_FAINT,
            font=theme.FONT_SM,
            justify="left",
            wraplength=680,
        ).pack(anchor="w", pady=(4, 0))

    def _build_mcp_entry(self) -> None:
        """MCP 入口。和插件的区别值得一句话说清：那边是本地代码，这边是外部进程。"""
        from ..mcp.manager import mcp_manager

        row = tk.Frame(self, bg=theme.BG_CARD)
        row.pack(fill="x", padx=20, pady=(8, 0))

        usage = mcp_manager.usage()
        count = len(mcp_manager.status())
        text = (
            f"MCP 服务器 {count} 个 · 注入 {usage['injected']} 个工具（{usage['tokens']} tok）"
            if count
            else "还没接 MCP 服务器 —— 接上之后能直接用外部工具进程（浏览器、数据库、SaaS）"
        )
        tk.Label(
            row,
            text=text,
            bg=theme.BG_CARD,
            fg=theme.TEXT_MUTED,
            font=theme.FONT_SM,
            anchor="w",
        ).pack(side="left", padx=12, pady=8)
        Pill(
            row,
            text="管理 MCP",
            command=self._open_mcp,
            variant="secondary",
            bg=theme.BG_CARD,
            height=28,
            padx=12,
        ).pack(side="right", padx=8, pady=6)

    def _open_mcp(self) -> None:
        from .mcp_dialog import MCPDialog

        MCPDialog(self, on_change=self._mcp_changed)

    def _mcp_changed(self) -> None:
        """MCP 变了：工具表重建 + 通知主窗口刷新系统提示。"""
        refresh_tools()
        self.refresh_list()
        if self.on_change:
            self.on_change(None)  # None = 这次变的是 MCP，不是插件

    def _build_installer(self) -> None:
        box = tk.Frame(self, bg=theme.BG)
        box.pack(fill="x", padx=20, pady=(8, 4))

        entry_wrap = tk.Frame(box, bg=theme.BG, highlightbackground=theme.BORDER_STRONG, highlightthickness=1)
        entry_wrap.pack(side="left", fill="x", expand=True)

        self.url_entry = tk.Entry(
            entry_wrap,
            bg=theme.BG,
            fg=theme.TEXT,
            font=theme.FONT_SM,
            relief="flat",
            bd=0,
            insertbackground=theme.TEXT,
        )
        self.url_entry.pack(fill="x", padx=12, pady=8)
        self.url_entry.insert(0, "https://github.com/owner/repo")
        self.url_entry.bind("<FocusIn>", self._clear_hint)
        self.url_entry.bind("<Return>", lambda e: self._install())
        self._hint_on = True

        self.install_btn = Pill(
            box,
            text="安装",
            command=self._install,
            variant="primary",
            height=32,
            padx=20,
        )
        self.install_btn.pack(side="left", padx=(8, 0))

        self.skill_btn = Pill(
            box,
            text="装为技能",
            command=self._install_skill,
            variant="secondary",
            bg=theme.BG,
            height=32,
            padx=16,
        )
        self.skill_btn.pack(side="left", padx=(8, 0))
        Tooltip(self.skill_btn, "同一地址，按技能安装：只取 Markdown 方法文档，不执行代码。", delay=300)

        self.status = tk.Label(box, text="", bg=theme.BG, fg=theme.TEXT_FAINT, font=theme.FONT_SM, anchor="w")
        self.status.pack(side="left", padx=(10, 0))

        tk.Label(
            self,
            text="支持：仓库地址、仓库子目录（/tree/main/sub）、单个文件、本地目录。「安装」装插件（.py），「装为技能」装方法文档（.md）",
            bg=theme.BG,
            fg=theme.TEXT_GHOST,
            font=theme.FONT_SM,
            anchor="w",
            wraplength=720,
            justify="left",
        ).pack(fill="x", padx=20, pady=(2, 8))

        Divider(self).pack(fill="x", padx=20)

    def _build_list(self) -> None:
        wrap = tk.Frame(self, bg=theme.BG)
        wrap.pack(fill="both", expand=True, padx=20, pady=(8, 4))

        self.canvas = tk.Canvas(wrap, bg=theme.BG, highlightthickness=0, bd=0)
        bar = tk.Scrollbar(wrap, command=self.canvas.yview, bd=0, width=9, troughcolor=theme.BG)
        self.canvas.configure(yscrollcommand=bar.set)
        bar.pack(side="right", fill="y")
        self.canvas.pack(side="left", fill="both", expand=True)

        self.rows = tk.Frame(self.canvas, bg=theme.BG)
        self._window = self.canvas.create_window((0, 0), window=self.rows, anchor="nw")
        self.rows.bind("<Configure>", lambda e: self.canvas.configure(scrollregion=self.canvas.bbox("all")))
        self.canvas.bind("<Configure>", lambda e: self.canvas.itemconfig(self._window, width=e.width))

        foot = tk.Frame(self, bg=theme.BG)
        foot.pack(fill="x", padx=20, pady=(0, 12))
        self.summary = tk.Label(foot, text="", bg=theme.BG, fg=theme.TEXT_MUTED, font=theme.FONT_SM)
        self.summary.pack(side="left")
        Pill(
            foot,
            text="打开插件目录",
            command=lambda: os.startfile(plugins.PLUGIN_DIR),  # noqa: S606 Windows 桌面打开目录
            variant="secondary",
            bg=theme.BG,
            height=28,
            padx=12,
        ).pack(side="right")

    # ------------------------------------------------------------ 列表

    def refresh_list(self) -> None:
        for child in self.rows.winfo_children():
            child.destroy()

        items = plugins.list_plugins()
        if not items:
            tk.Label(
                self.rows,
                text="还没有插件。上面填个 GitHub 地址就能装。",
                bg=theme.BG,
                fg=theme.TEXT_GHOST,
                font=theme.FONT_SM,
            ).pack(anchor="w", pady=12)
            self.summary.configure(text="已安装 0 个")
            return

        enabled = 0
        for plugin in items:
            enabled += self._row(plugin)

        self.summary.configure(text=f"已安装 {len(items)} 个 · 启用 {enabled} 个")

    def _row(self, plugin) -> int:
        row = tk.Frame(self.rows, bg=theme.BG)
        row.pack(fill="x", pady=6)

        left = tk.Frame(row, bg=theme.BG)
        left.pack(side="left", fill="x", expand=True)

        title = tk.Frame(left, bg=theme.BG)
        title.pack(anchor="w")
        tk.Label(title, text=plugin.name, bg=theme.BG, fg=theme.TEXT, font=theme.FONT_BODY_BOLD).pack(side="left")
        tk.Label(
            title,
            text=f"  v{plugin.version}  ·  {len(plugin.tools)} 个工具",
            bg=theme.BG,
            fg=theme.TEXT_FAINT,
            font=theme.FONT_SM,
        ).pack(side="left")

        if plugin.error:
            tk.Label(
                left, text=plugin.error, bg=theme.BG, fg=theme.DANGER, font=theme.FONT_SM, anchor="w",
                wraplength=520, justify="left",
            ).pack(anchor="w", pady=(2, 0))
        elif plugin.tools:
            tk.Label(
                left,
                text="工具：" + "、".join(plugin.tools),
                bg=theme.BG,
                fg=theme.TEXT_MUTED,
                font=theme.FONT_SM,
                anchor="w",
                wraplength=520,
                justify="left",
            ).pack(anchor="w", pady=(2, 0))

        if plugin.source:
            tk.Label(
                left, text=plugin.source, bg=theme.BG, fg=theme.TEXT_GHOST, font=theme.FONT_SM, anchor="w",
                wraplength=520, justify="left",
            ).pack(anchor="w")

        right = tk.Frame(row, bg=theme.BG)
        right.pack(side="right")

        var = tk.BooleanVar(value=plugin.enabled)
        Toggle(right, var, command=lambda n=plugin.name, v=var: self._toggle(n, v)).pack(side="left", padx=(0, 10))

        Pill(
            right,
            text="卸载",
            command=lambda n=plugin.name: self._remove(n),
            variant="secondary",
            bg=theme.BG,
            height=26,
            padx=10,
        ).pack(side="left")

        Divider(self.rows).pack(fill="x", pady=2)
        return 1 if plugin.enabled else 0

    # ------------------------------------------------------------ 动作

    def _clear_hint(self, _event=None) -> None:
        if self._hint_on:
            self.url_entry.delete(0, "end")
            self._hint_on = False

    def _install(self) -> None:
        url = self.url_entry.get().strip()
        if not url or url.startswith("https://github.com/owner/repo"):
            self.status.configure(text="先填个地址", fg=theme.DANGER)
            return

        confirmed = messagebox.askyesno(
            "确认安装",
            f"将从这里安装插件：\n\n{url}\n\n插件是第三方代码，安装等于允许它在这台机器上运行。\n只装你信任的仓库。继续吗？",
            parent=self,
        )
        if not confirmed:
            return

        self.status.configure(text="安装中…", fg=theme.TEXT_MUTED)
        self.install_btn.set_enabled(False, fill=theme.BORDER_STRONG)
        threading.Thread(target=self._install_worker, args=(url,), daemon=True).start()
        self.after(120, self._poll)

    def _install_worker(self, url: str) -> None:
        try:
            plugin = plugins.install(url)
            self.q.put(("ok", plugin))
        except Exception as exc:  # noqa: BLE001 安装失败要原样告诉用户
            self.q.put(("err", str(exc)))

    def _install_skill(self) -> None:
        url = self.url_entry.get().strip()
        if not url or url.startswith("https://github.com/owner/repo"):
            self.status.configure(text="先填个地址", fg=theme.DANGER)
            return

        confirmed = messagebox.askyesno(
            "确认安装技能",
            f"将从这里安装技能：\n\n{url}\n\n技能是纯 Markdown 方法文档，不会执行任何代码。\n继续吗？",
            parent=self,
        )
        if not confirmed:
            return

        self.status.configure(text="安装中…", fg=theme.TEXT_MUTED)
        self.skill_btn.set_enabled(False, fg=theme.TEXT_GHOST)
        threading.Thread(target=self._skill_worker, args=(url,), daemon=True).start()
        self.after(120, self._poll)

    def _skill_worker(self, url: str) -> None:
        try:
            keys = skills.install(url)
            self.q.put(("skill_ok", keys))
        except Exception as exc:  # noqa: BLE001
            self.q.put(("err", str(exc)))

    def _poll(self) -> None:
        try:
            kind, payload = self.q.get_nowait()
        except queue.Empty:
            self.after(150, self._poll)
            return

        self.install_btn.set_enabled(True, fill=theme.ACCENT)
        self.skill_btn.set_enabled(True, fg=theme.TEXT_MUTED)
        if kind == "ok":
            tools = refresh_tools()
            self.status.configure(text=f"已安装 {payload.name}", fg=theme.SUCCESS)
            self.refresh_list()
            if self.on_change:
                self.on_change(tools)
        elif kind == "skill_ok":
            self.status.configure(text=f"已装 {len(payload)} 个技能：{'、'.join(payload)}", fg=theme.SUCCESS)
            if self.on_change:
                self.on_change(None)  # 技能清单在系统提示词里，让主窗口刷新
        else:
            self.status.configure(text=payload[:80], fg=theme.DANGER)

    def _toggle(self, name: str, var: tk.BooleanVar) -> None:
        plugins.set_enabled(name, var.get())
        tools = refresh_tools()
        self.refresh_list()
        if self.on_change:
            self.on_change(tools)

    def _remove(self, name: str) -> None:
        if not messagebox.askyesno("卸载插件", f"确定卸载 {name}？它的工具会立刻失效。", parent=self):
            return
        try:
            plugins.remove(name)
        except Exception as exc:  # noqa: BLE001
            messagebox.showerror("卸载失败", str(exc), parent=self)
            return
        tools = refresh_tools()
        self.refresh_list()
        self.status.configure(text=f"已卸载 {name}", fg=theme.TEXT_MUTED)
        if self.on_change:
            self.on_change(tools)
