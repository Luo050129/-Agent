"""交互层：设置窗口。

外观（主题 / 字体 / 字号）+ 插件入口。
改外观必须重建主窗口 —— Tk 控件在构造时就把颜色和字体定死了，
所以这里的做法是「改完保存，回调让主窗口自己拆了重搭」。
"""

from __future__ import annotations

import tkinter as tk
from tkinter import ttk

from .. import plugins
from ..model import all_specs, mainstream_specs
from ..model.custom import VALID_PROTOCOLS
from ..model.custom import default_key_env
from ..model.custom import add as add_custom
from ..model.custom import build as custom_build
from ..model.custom import load as load_custom
from ..model.custom import remove as remove_custom
from . import theme
from .settings import MAX_SCALE, MIN_SCALE, THEME_LABELS, THEMES, UISettings
from .widgets import Divider, Pill, ScrollFrame


class SettingsDialog(tk.Toplevel):
    def __init__(
        self,
        master: tk.Misc,
        prefs: UISettings,
        on_apply=None,
        on_plugins=None,
        on_model=None,
        on_add_model=None,
        current_model: str = "",
    ) -> None:
        super().__init__(master, bg=theme.BG)
        self.on_apply = on_apply
        self.on_plugins = on_plugins
        self.on_model = on_model
        self.on_add_model = on_add_model
        self.draft = UISettings(
            theme=prefs.theme, font_family=prefs.font_family, font_scale=prefs.font_scale
        )
        self._theme_boxes: dict[str, tk.Frame] = {}
        self._current_key = (current_model or "").strip()

        self.title("设置")
        self.geometry(theme.dialog_geometry(self, 640, 700))
        self.minsize(theme.scale_px(560), theme.scale_px(600))
        self.transient(master)

        self._build_header()
        self._build_footer()  # 先占住底部，后面滚动区才好填满中间
        self.scroll = ScrollFrame(self, bg=theme.BG)
        self.scroll.pack(fill="both", expand=True)
        self._build_appearance()
        self._build_model()
        self._build_plugins()

    # ------------------------------------------------------------ 结构

    def _build_header(self) -> None:
        head = tk.Frame(self, bg=theme.BG)
        head.pack(fill="x", padx=22, pady=(18, 4))
        tk.Label(head, text="设置", bg=theme.BG, fg=theme.TEXT, font=theme.FONT_BRAND).pack(anchor="w")
        tk.Label(
            head,
            text="外观保存后会重建窗口，对话记录保留。",
            bg=theme.BG,
            fg=theme.TEXT_FAINT,
            font=theme.FONT_SM,
        ).pack(anchor="w", pady=(3, 0))

    def _section(self, title: str) -> tk.Frame:
        parent = self.scroll.body
        Divider(parent).pack(fill="x", padx=22, pady=(16, 0))
        body = tk.Frame(parent, bg=theme.BG)
        body.pack(fill="x", padx=22, pady=(10, 0))
        tk.Label(body, text=title, bg=theme.BG, fg=theme.TEXT, font=theme.FONT_H).pack(anchor="w")
        inner = tk.Frame(body, bg=theme.BG)
        inner.pack(fill="x", pady=(8, 0))
        return inner

    def _build_appearance(self) -> None:
        inner = self._section("外观")

        tk.Label(inner, text="主题", bg=theme.BG, fg=theme.TEXT_MUTED, font=theme.FONT_SM).pack(anchor="w")
        row = tk.Frame(inner, bg=theme.BG)
        row.pack(fill="x", pady=(6, 14))
        for name in THEMES:
            self._theme_card(row, name)

        tk.Label(inner, text="字体", bg=theme.BG, fg=theme.TEXT_MUTED, font=theme.FONT_SM).pack(anchor="w")
        self.font_box = ttk.Combobox(inner, values=self._font_choices(), state="readonly", width=32)
        self.font_box.set(self.draft.font_family or theme.FONT_BODY[0])
        self.font_box.pack(anchor="w", pady=(6, 14))
        self.font_box.bind("<<ComboboxSelected>>", lambda e: self._refresh_preview())

        tk.Label(inner, text="字号", bg=theme.BG, fg=theme.TEXT_MUTED, font=theme.FONT_SM).pack(anchor="w")
        scale_row = tk.Frame(inner, bg=theme.BG)
        scale_row.pack(fill="x", pady=(6, 12))
        self.scale_value = tk.Label(
            scale_row,
            text=f"{int(self.draft.font_scale * 100)}%",
            bg=theme.BG,
            fg=theme.ACCENT_DEEP,
            font=theme.FONT_SM,
            width=5,
            anchor="e",
        )
        self.scale_value.pack(side="right")
        self.scale = ttk.Scale(
            scale_row,
            from_=MIN_SCALE,
            to=MAX_SCALE,
            value=self.draft.font_scale,
            command=self._on_scale,
        )
        self.scale.pack(side="left", fill="x", expand=True, padx=(0, 10))

        self.preview = tk.Label(
            inner,
            text="预览：Agent 会读取目录、解析文档，然后给出结论。",
            bg=theme.BG_CARD,
            fg=theme.TEXT,
            font=theme.FONT_BODY,
            anchor="w",
            justify="left",
            padx=12,
            pady=10,
        )
        self.preview.pack(fill="x")

    def _build_model(self) -> None:
        """模型选择从侧栏挪到这里。默认只列主流，冷门的收在勾选框后面。"""
        inner = self._section("模型")

        row = tk.Frame(inner, bg=theme.BG)
        row.pack(fill="x")
        tk.Label(row, text="当前使用", bg=theme.BG, fg=theme.TEXT_MUTED, font=theme.FONT_SM).pack(side="left")

        self._all_specs = list(all_specs())
        keep = {spec.key for spec in mainstream_specs()}
        self._mainstream = [spec for spec in self._all_specs if spec.key in keep]
        self.model_specs = self._mainstream

        self.model_box = ttk.Combobox(row, state="readonly", width=32)
        self.model_box.pack(side="left", padx=(10, 0))
        self.model_box.bind("<<ComboboxSelected>>", self._on_model_pick)

        self.add_model_btn = Pill(
            row,
            text="＋ 添加模型",
            command=self._add_model,
            variant="secondary",
            bg=theme.BG,
            height=28,
            padx=12,
        )
        self.show_all = tk.BooleanVar(value=False)
        opts = tk.Frame(inner, bg=theme.BG)
        opts.pack(fill="x", pady=(8, 0))
        tk.Checkbutton(
            opts,
            text="显示全部模型",
            variable=self.show_all,
            command=self._fill_models,
            bg=theme.BG,
            fg=theme.TEXT_MUTED,
            activebackground=theme.BG,
            activeforeground=theme.TEXT,
            selectcolor=theme.BG_SOFT,
            font=theme.FONT_SM,
            bd=0,
            highlightthickness=0,
            cursor="hand2",
        ).pack(side="left")

        self.add_model_btn = Pill(
            opts,
            text="＋ 添加模型",
            command=self._add_model,
            variant="secondary",
            bg=theme.BG,
            height=28,
            padx=12,
        )
        self.add_model_btn.pack(side="right")

        self.model_hint = tk.Label(
            inner, text="", bg=theme.BG, fg=theme.TEXT_GHOST, font=theme.FONT_SM, anchor="w"
        )
        self.model_hint.pack(fill="x", pady=(2, 0))
        self._fill_models()

    def _fill_models(self) -> None:
        """按勾选状态换列表。切过去之后如果当前模型不在列表里，就往上补一个。"""
        self.model_specs = self._all_specs if self.show_all.get() else self._mainstream

        labels = [spec.key for spec in self.model_specs]
        self.model_box.configure(values=labels)

        index = next((i for i, spec in enumerate(self.model_specs) if spec.key == self._current_key), 0)
        if labels:
            self.model_box.current(index)

        scope = "全部" if self.show_all.get() else "常用"
        current = next(
            (s for s in self.model_specs if s.key == self._current_key), None
        )
        where = f"当前 {current.provider}" if current else "未选中内置模型"
        self.model_hint.configure(
            text=f"{scope} {len(labels)} 个 · {where}。选中立即生效，缺 Key 会提示去 .env 补。"
        )

    def _on_model_pick(self, _event=None) -> None:
        index = self.model_box.current()
        if not (0 <= index < len(self.model_specs)) or not self.on_model:
            return
        self.on_model(self.model_specs[index].key)

    def _build_plugins(self) -> None:
        inner = self._section("插件")
        installed = list(plugins.list_plugins())
        tools = sum(len(p.tools) for p in installed)

        line = tk.Frame(inner, bg=theme.BG)
        line.pack(fill="x")
        text = f"已装 {len(installed)} 个插件，提供 {tools} 个工具。" if installed else "还没有安装插件。"
        tk.Label(line, text=text, bg=theme.BG, fg=theme.TEXT_MUTED, font=theme.FONT_SM).pack(side="left")
        Pill(
            line, text="管理插件", command=self._manage_plugins, variant="secondary",
            bg=theme.BG, height=30, padx=14,
        ).pack(side="right")

    def _build_footer(self) -> None:
        foot = tk.Frame(self, bg=theme.BG)
        foot.pack(side="bottom", fill="x", padx=22, pady=16)
        Pill(foot, text="保存并应用", command=self._save, variant="primary", bg=theme.BG).pack(side="right")
        Pill(
            foot, text="取消", command=self.destroy, variant="secondary", bg=theme.BG
        ).pack(side="right", padx=(0, 10))

    # ------------------------------------------------------------ 交互

    def _font_choices(self) -> list[str]:
        try:
            import tkinter.font as tkfont

            names = sorted({n for n in tkfont.families(self) if not n.startswith("@")})
        except Exception:  # noqa: BLE001 拿不到就用当前字体
            names = []
        return names or [theme.FONT_BODY[0]]

    def _theme_card(self, parent: tk.Frame, name: str) -> None:
        """主题卡片用自己的配色画，这样一眼能看出选的是哪套。"""
        palette = theme.PALETTES[name]
        card = tk.Frame(
            parent,
            bg=palette["BG"],
            highlightthickness=2,
            highlightbackground=theme.ACCENT if name == self.draft.theme else theme.BORDER,
            cursor="hand2",
        )
        card.pack(side="left", padx=(0, 10))
        self._theme_boxes[name] = card

        swatches = tk.Frame(card, bg=palette["BG"])
        swatches.pack(padx=10, pady=(9, 4))
        for color in (palette["ACCENT"], palette["TEXT"], palette["BG_CARD"]):
            tk.Frame(swatches, bg=color, width=16, height=16).pack(side="left", padx=2)

        label = tk.Label(
            card, text=THEME_LABELS.get(name, name), bg=palette["BG"], fg=palette["TEXT"], font=theme.FONT_SM
        )
        label.pack(padx=10, pady=(0, 8))

        for widget in (card, swatches, label):
            widget.bind("<Button-1>", lambda e, n=name: self._pick_theme(n))

    def _pick_theme(self, name: str) -> None:
        self.draft.theme = name
        for key, box in self._theme_boxes.items():
            box.configure(highlightbackground=theme.ACCENT if key == name else theme.BORDER)

    def _on_scale(self, raw) -> None:
        try:
            value = float(raw)
        except (TypeError, ValueError):
            return
        self.draft.font_scale = max(MIN_SCALE, min(MAX_SCALE, value))
        self.scale_value.configure(text=f"{int(self.draft.font_scale * 100)}%")
        self._refresh_preview()

    def _refresh_preview(self) -> None:
        family = self.font_box.get() or theme.FONT_BODY[0]
        size = max(8, round(11 * self.draft.font_scale))
        self.preview.configure(font=(family, size))

    def _add_model(self) -> None:
        if self.on_add_model:
            self.on_add_model()

    def _manage_plugins(self) -> None:
        if self.on_plugins:
            self.on_plugins()

    def _save(self) -> None:
        self.draft.font_family = self.font_box.get() or ""
        self.draft = self.draft.normalized()
        if self.on_apply:
            self.on_apply(self.draft)
        self.destroy()


class ModelDialog(tk.Toplevel):
    """添加自定义模型。

    只让人填三样：模型 id、显示名（可空）、接口地址。
    供应商、协议、Key 环境变量都跟着「服务商」这一排预设走 ——
    以前六个字段挨个填，其实五个都能推出来。
    """

    # (按钮文字, 供应商, 协议, 接口地址, Key 环境变量)
    PRESETS = (
        ("DeepSeek", "DeepSeek", "openai", "https://api.deepseek.com/v1", "DEEPSEEK_API_KEY"),
        ("OpenAI", "OpenAI", "openai", "https://api.openai.com/v1", "OPENAI_API_KEY"),
        ("Claude", "Anthropic", "anthropic", "https://api.anthropic.com/v1", "ANTHROPIC_API_KEY"),
        (
            "Gemini",
            "Google",
            "gemini",
            "https://generativelanguage.googleapis.com/v1beta",
            "GEMINI_API_KEY",
        ),
        ("Kimi", "Moonshot", "openai", "https://api.moonshot.cn/v1", "MOONSHOT_API_KEY"),
        ("本地 Ollama", "本地", "openai", "http://127.0.0.1:11434/v1", ""),
        ("自定义", "自定义", "openai", "", ""),
    )

    def __init__(self, master: tk.Misc, on_added=None) -> None:
        super().__init__(master, bg=theme.BG)
        self.on_added = on_added
        self.vars: dict[str, tk.StringVar] = {}
        self._entries: dict[str, tk.Entry] = {}
        self.protocol_var = tk.StringVar(value="openai")
        self._preset = self.PRESETS[0]
        self._chips: dict[str, Pill] = {}

        self.title("添加模型")
        self.geometry(theme.dialog_geometry(self, 640, 560))
        self.minsize(theme.scale_px(580), theme.scale_px(500))
        self.transient(master)

        self._build_form()
        self._build_existing()
        self._pick_preset(self._preset)

    # ------------------------------------------------------------ 表单

    def _build_form(self) -> None:
        head = tk.Frame(self, bg=theme.BG)
        head.pack(fill="x", padx=22, pady=(18, 6))
        tk.Label(head, text="添加模型", bg=theme.BG, fg=theme.TEXT, font=theme.FONT_BRAND).pack(anchor="w")
        tk.Label(
            head,
            text="先选服务商，接口地址会自己填好；再填模型 id 就行。",
            bg=theme.BG,
            fg=theme.TEXT_FAINT,
            font=theme.FONT_SM,
        ).pack(anchor="w", pady=(3, 0))

        chips = tk.Frame(self, bg=theme.BG)
        chips.pack(fill="x", padx=22, pady=(10, 0))
        for row_start in range(0, len(self.PRESETS), 4):
            line = tk.Frame(chips, bg=theme.BG)
            line.pack(fill="x", pady=(0, 6))
            for preset in self.PRESETS[row_start : row_start + 4]:
                label = preset[0]
                self._chips[label] = Pill(
                    line,
                    text=label,
                    command=lambda p=preset: self._pick_preset(p),
                    variant="secondary",
                    bg=theme.BG,
                    height=28,
                    padx=12,
                )
                self._chips[label].pack(side="left", padx=(0, 8))

        form = tk.Frame(self, bg=theme.BG)
        form.pack(fill="x", padx=22, pady=(8, 0))
        form.columnconfigure(1, weight=1)

        fields = (
            ("model", "模型 id", "请求体里的 model，例如 deepseek-chat、gpt-4o"),
            ("key", "显示名", "菜单里看到的名字。留空就用模型 id"),
            ("base_url", "接口地址", "填到 /v1 即可，程序会自己拼端点"),
        )
        row = 0
        for name, label, hint in fields:
            tk.Label(form, text=label, bg=theme.BG, fg=theme.TEXT_MUTED, font=theme.FONT_SM).grid(
                row=row, column=0, sticky="w", pady=(0, 2)
            )
            var = tk.StringVar()
            self.vars[name] = var
            entry = tk.Entry(
                form,
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
            entry.grid(row=row, column=1, sticky="ew", padx=(12, 0), pady=(0, 2), ipady=5)
            entry.bind("<Return>", lambda e: self._add())
            self._entries[name] = entry
            tk.Label(form, text=hint, bg=theme.BG, fg=theme.TEXT_GHOST, font=theme.FONT_SM).grid(
                row=row + 1, column=1, sticky="w", padx=(12, 0), pady=(0, 8)
            )
            row += 2

        tk.Label(form, text="协议", bg=theme.BG, fg=theme.TEXT_MUTED, font=theme.FONT_SM).grid(
            row=row, column=0, sticky="w"
        )
        box = ttk.Combobox(
            form,
            textvariable=self.protocol_var,
            values=list(VALID_PROTOCOLS),
            state="readonly",
            width=16,
        )
        box.grid(row=row, column=1, sticky="w", padx=(12, 0), ipady=3)
        row += 1

        self.key_hint = tk.Label(
            form, text="", bg=theme.BG, fg=theme.TEXT_GHOST, font=theme.FONT_SM, anchor="w"
        )
        self.key_hint.grid(row=row, column=1, sticky="w", padx=(12, 0), pady=(8, 0))
        self.vars["key"].trace_add("write", lambda *_: self._refresh_key_hint())
        self.vars["model"].trace_add("write", lambda *_: self._refresh_key_hint())

        btns = tk.Frame(self, bg=theme.BG)
        btns.pack(fill="x", padx=22, pady=(14, 0))
        Pill(btns, text="添加", command=self._add, variant="primary", bg=theme.BG).pack(side="right")
        Pill(btns, text="关闭", command=self.destroy, variant="secondary", bg=theme.BG).pack(
            side="right", padx=(0, 10)
        )
        self.status = tk.Label(btns, text="", bg=theme.BG, fg=theme.TEXT_MUTED, font=theme.FONT_SM)
        self.status.pack(side="left")

    # ------------------------------------------------------------ 预设

    def _pick_preset(self, preset) -> None:
        """选中一个服务商：地址、协议、供应商一起带出来，用户只需要填模型 id。"""
        label, _provider, protocol, base_url, _key_env = preset
        self._preset = preset
        self.protocol_var.set(protocol)
        self.vars["base_url"].set(base_url)
        for name, chip in self._chips.items():
            chip.set_style(variant="primary" if name == label else "secondary")
        self._refresh_key_hint()
        self.vars["model"].set("")
        entry = self._entries.get("model")
        if entry is not None:
            entry.focus_set()

    def _key_env(self) -> str:
        preset_env = self._preset[4]
        if preset_env:
            return preset_env
        name = self.vars["key"].get().strip() or self.vars["model"].get().strip()
        return default_key_env(name) if name else ""

    def _refresh_key_hint(self) -> None:
        env = self._key_env()
        if env:
            self.key_hint.configure(text=f"Key 从环境变量 {env} 读，没配的话切过去会提示。")
        else:
            self.key_hint.configure(text="填了模型 id 就知道 Key 该放哪个环境变量。")

    def _build_existing(self) -> None:
        Divider(self).pack(fill="x", padx=22, pady=(16, 0))
        body = tk.Frame(self, bg=theme.BG)
        body.pack(fill="both", expand=True, padx=22, pady=(10, 16))
        tk.Label(body, text="已添加的自定义模型", bg=theme.BG, fg=theme.TEXT, font=theme.FONT_H).pack(
            anchor="w"
        )
        self.list_frame = tk.Frame(body, bg=theme.BG)
        self.list_frame.pack(fill="both", expand=True, pady=(8, 0))
        self._refresh_existing()

    def _refresh_existing(self) -> None:
        for child in self.list_frame.winfo_children():
            child.destroy()

        specs = load_custom()
        if not specs:
            tk.Label(
                self.list_frame, text="还没有，用上面的表单加一个。",
                bg=theme.BG, fg=theme.TEXT_GHOST, font=theme.FONT_SM, anchor="w",
            ).pack(fill="x")
            return

        for spec in specs:
            row = tk.Frame(self.list_frame, bg=theme.BG)
            row.pack(fill="x", pady=3)
            tk.Label(
                row,
                text=f"{spec.key}",
                bg=theme.BG,
                fg=theme.TEXT,
                font=theme.FONT_BODY_BOLD,
                anchor="w",
                width=16,
            ).pack(side="left")
            tk.Label(
                row,
                text=f"{spec.protocol} · {spec.model}",
                bg=theme.BG,
                fg=theme.TEXT_FAINT,
                font=theme.FONT_SM,
                anchor="w",
            ).pack(side="left", fill="x", expand=True)
            Pill(
                row,
                text="删除",
                command=lambda k=spec.key: self._remove(k),
                variant="danger",
                bg=theme.BG,
                height=26,
                padx=12,
            ).pack(side="right")

    def _add(self) -> None:
        model_id = self.vars["model"].get().strip()
        display = self.vars["key"].get().strip() or model_id
        base_url = self.vars["base_url"].get().strip()
        provider = self._preset[1] or "自定义"

        spec = custom_build(
            key=display,
            provider=provider,
            protocol=self.protocol_var.get(),
            model=model_id,
            base_url=base_url,
            key_env=self._key_env(),
        )
        if spec is None:
            self._say("模型 id、接口地址都不能为空", theme.DANGER)
            return

        ok, message = add_custom(spec)
        self._say(message, theme.SUCCESS if ok else theme.DANGER)
        if not ok:
            return
        self._refresh_existing()
        self.vars["model"].set("")
        self.vars["key"].set("")
        if self.on_added:
            self.on_added(spec)

    def _remove(self, key: str) -> None:
        if remove_custom(key):
            self._say(f"已删除 {key}", theme.TEXT_MUTED)
            self._refresh_existing()

    def _say(self, text: str, color: str) -> None:
        self.status.configure(text=text, fg=color)
