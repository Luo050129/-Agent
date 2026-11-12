"""交互层：Tkinter 没有圆角，只能用 Canvas 画。

这里放几个小部件：圆角矩形、胶囊按钮、开关、悬浮提示。别的地方直接复用。
"""

from __future__ import annotations

import math
import tkinter as tk
import tkinter.font as tkfont

from . import theme


class Tooltip:
    """鼠标悬停提示。

    Tkinter 没有原生 tooltip，用 overrideredirect 的 Toplevel 自己画一个。
    延迟弹出，鼠标移开就销毁 —— 别做成常驻，会挡住下面的东西。
    """

    def __init__(
        self,
        widget,
        text: str | callable,
        *,
        delay: int = 350,
        wraplength: int = 300,
    ) -> None:
        self.widget = widget
        self._text = text
        self.delay = delay
        self.wraplength = wraplength
        self._after_id: str | None = None
        self._tip: tk.Toplevel | None = None

        widget.bind("<Enter>", self._schedule, add="+")
        widget.bind("<Leave>", self._hide, add="+")
        # 点一下就藏起来，免得弹窗盖住正在操作的元素
        widget.bind("<Button-1>", self._hide, add="+")

    # ------------------------------------------------------------ 内部

    def _resolve(self) -> str:
        return self._text() if callable(self._text) else str(self._text)

    def _schedule(self, _event=None) -> None:
        self._cancel()
        self._after_id = self.widget.after(self.delay, self._show)

    def _cancel(self) -> None:
        if self._after_id is not None:
            try:
                self.widget.after_cancel(self._after_id)
            except tk.TclError:
                pass
            self._after_id = None

    def _show(self) -> None:
        self._after_id = None
        text = self._resolve().strip()
        if not text or self._tip is not None:
            return

        try:
            x = self.widget.winfo_rootx()
            y = self.widget.winfo_rooty() + self.widget.winfo_height() + 8
        except tk.TclError:
            return

        tip = tk.Toplevel(self.widget)
        tip.wm_overrideredirect(True)
        tip.configure(bg=theme.BORDER_STRONG)
        try:
            tip.attributes("-topmost", True)
        except tk.TclError:
            pass

        body = tk.Label(
            tip,
            text=text,
            justify="left",
            bg=theme.BG,
            fg=theme.TEXT,
            font=theme.FONT_SM,
            wraplength=self.wraplength,
            padx=12,
            pady=9,
        )
        body.pack(padx=1, pady=1)

        # 贴右边就左移，别跑出屏幕
        tip.update_idletasks()
        tip_w = tip.winfo_width()
        screen_w = tip.winfo_screenwidth()
        if x + tip_w > screen_w - 12:
            x = max(12, screen_w - tip_w - 12)
        tip.geometry(f"+{x}+{y}")
        self._tip = tip

    def _hide(self, _event=None) -> None:
        self._cancel()
        if self._tip is not None:
            try:
                self._tip.destroy()
            except tk.TclError:
                pass
            self._tip = None


# ---------------------------------------------------------------- 抗锯齿图形
#
# Tk 的 Canvas 画椭圆和圆角多边形都不带抗锯齿，高 DPI 下边缘全是硬台阶。
# PhotoImage 又只有二值透明，做不了半透明边缘。
#
# 所以：用有符号距离场算出每个像素的覆盖率，把边缘像素和背景色预先混合。
# 背景是纯色（侧栏/输入卡），主题一换就重建窗口重新渲染，正好不会串色。

def _rgb(color: str) -> tuple[int, int, int]:
    color = color.lstrip("#")
    return int(color[0:2], 16), int(color[2:4], 16), int(color[4:6], 16)


def hex_color(widget, color: str) -> str:
    """把 Tk 认识的任何颜色写成 #rrggbb。

    `master.cget("bg")` 可能是「SystemButtonFace」这种系统色名，
    直接拿去算混色会崩，先让 Tk 自己解析成 RGB。
    """
    if isinstance(color, str) and color.startswith("#") and len(color) == 7:
        return color
    try:
        r, g, b = widget.winfo_rgb(color)
    except (tk.TclError, ValueError):
        return "#FFFFFF"
    return "#{:02x}{:02x}{:02x}".format(r // 256, g // 256, b // 256)


def _mix(front: str, back: str, alpha: float) -> str:
    """把 front 按 alpha 叠到 back 上。"""
    if alpha >= 0.999:
        return front
    if alpha <= 0.001:
        return back
    fr, fg, fb = _rgb(front)
    br, bg, bb = _rgb(back)
    return "#{:02x}{:02x}{:02x}".format(
        round(fr * alpha + br * (1 - alpha)),
        round(fg * alpha + bg * (1 - alpha)),
        round(fb * alpha + bb * (1 - alpha)),
    )


def _sdf_round_rect(x: float, y: float, w: int, h: int, radius: float) -> float:
    """点到圆角矩形边界的有符号距离，里面是负数。半径取 h/2 就是胶囊。"""
    qx = abs(x - w / 2) - (w / 2 - radius)
    qy = abs(y - h / 2) - (h / 2 - radius)
    return math.hypot(max(qx, 0.0), max(qy, 0.0)) + min(max(qx, qy), 0.0) - radius


def _coverage(distance: float) -> float:
    """距离场转覆盖率：边界附近一像素宽的过渡带。"""
    return min(1.0, max(0.0, 0.5 - distance))


def _photo_from_rows(rows: list[list[str]], w: int, h: int) -> tk.PhotoImage:
    photo = tk.PhotoImage(width=w, height=h)
    photo.put("{" + "} {".join(" ".join(row) for row in rows) + "}", to=(0, 0, w, h))
    return photo


def render_round_rect(
    w: int,
    h: int,
    radius: float,
    fill: str,
    bg: str,
    outline: str = "",
    border: int = 1,
) -> tk.PhotoImage:
    """抗锯齿的圆角矩形。radius 取 h/2 就是圆形/胶囊。

    `outline` 用距离场往里偏移 `border` 像素得到内圈，外圈留描边色 ——
    两个形状直接叠会互相盖住抗锯齿边缘，用距离场偏移不会。
    """
    w, h = max(1, w), max(1, h)
    radius = max(0.0, min(radius, w / 2, h / 2))
    rows = []
    for y in range(h):
        row = []
        for x in range(w):
            px, py = x + 0.5, y + 0.5
            distance = _sdf_round_rect(px, py, w, h, radius)
            outer = _coverage(distance)
            if outer <= 0:
                row.append(bg)
                continue
            if outline:
                inner = _coverage(distance + border)
                color = _mix(fill, outline, inner)
            else:
                color = fill
            row.append(_mix(color, bg, outer))
        rows.append(row)
    return _photo_from_rows(rows, w, h)


def render_sidebar_toggle(w: int, h: int, color: str, bg: str) -> tk.PhotoImage:
    """侧栏开关图标：圆角方框 + 左侧实心列。

    不用字体符号（☰ / ◧ 这类在部分字体里会掉成方框），
    也不用心画线（Canvas 线条没有抗锯齿），整个按距离场渲染。
    """
    w, h = max(8, w), max(8, h)
    pad = max(1.0, w * 0.14)
    bw, bh = w - pad * 2, h - pad * 2
    radius = bh * 0.30
    stroke = max(1.2, w * 0.075)
    col_w = bw * 0.34

    rows = []
    for y in range(h):
        row = []
        for px, py in ((x + 0.5, y + 0.5) for x in range(w)):
            distance = _sdf_round_rect(px - pad, py - pad, bw, bh, radius)
            outer = _coverage(distance)
            inner = _coverage(distance + stroke)
            alpha = max(0.0, outer - inner)
            if inner > 0 and col_w > 1:
                col = _coverage(_sdf_round_rect(px - pad, py - pad, col_w, bh, max(1.0, radius * 0.5)))
                alpha = max(alpha, col * inner)
            row.append(_mix(color, bg, alpha))
        rows.append(row)
    return _photo_from_rows(rows, w, h)


def luminance(color: str) -> float:
    """0 黑 1 白，用来决定文字该用深色还是白色。"""
    r, g, b = _rgb(color)
    return (0.299 * r + 0.587 * g + 0.114 * b) / 255


def auto_fg(fill: str) -> str:
    """底色亮就用深色字，底色暗就用白字。

    不这么兜一下，「浅灰底 + 默认白字」的按钮会直接看不见。
    """
    return theme.TEXT if luminance(fill) > 0.62 else theme.ON_ACCENT


def round_photo(path, size: int, radius: float | None = None):
    """缩到 size 并把圆角（或圆形）外面刷成透明。

    Tk 不能裁剪图片，只能逐像素 `transparency_set`。size 不超过 128 时
    开销可忽略。失败返回 None，调用方自己退回文字。
    """
    try:
        photo = tk.PhotoImage(file=str(path))
    except (tk.TclError, OSError):
        return None

    factor = max(1, round(photo.width() / size))
    if factor > 1:
        photo = photo.subsample(factor, factor)

    width, height = photo.width(), photo.height()
    if radius is None:
        radius = min(width, height) / 2 - 0.5
    radius = min(radius, width / 2, height / 2)

    try:
        for y in range(height):
            for x in range(width):
                if _sdf_round_rect(x + 0.5, y + 0.5, width, height, radius) > 0:
                    photo.transparency_set(x, y, True)
    except tk.TclError:  # 极老版本的 Tk 没有 transparency_set
        return photo
    return photo


def render_toggle(w: int, h: int, on: bool, bg: str) -> tk.PhotoImage:
    """开关的整体图：胶囊轨道 + 白色圆点。开和关各渲染一张。"""
    track = theme.ACCENT if on else theme.BORDER_STRONG
    pad = max(2, round(h * 0.14))
    knob = h - pad * 2
    knob_x = (w - pad - knob / 2) if on else (pad + knob / 2)
    cy = h / 2

    rows = []
    for y in range(h):
        row = []
        for x in range(w):
            px, py = x + 0.5, y + 0.5
            color = _mix(track, bg, _coverage(_sdf_round_rect(px, py, w, h, h / 2)))
            alpha = _coverage(math.hypot(px - knob_x, py - cy) - knob / 2)
            if alpha > 0:
                color = _mix("#FFFFFF", color, alpha)
            row.append(color)
        rows.append(row)
    return _photo_from_rows(rows, w, h)


def round_rect(canvas: tk.Canvas, x1, y1, x2, y2, r, **kw):
    """用平滑多边形模拟圆角矩形。"""
    r = min(r, (x2 - x1) / 2, (y2 - y1) / 2)
    pts = [
        x1 + r, y1, x2 - r, y1, x2, y1, x2, y1 + r,
        x2, y2 - r, x2, y2, x2 - r, y2, x1 + r, y2,
        x1, y2, x1, y2 - r, x1, y1 + r, x1, y1,
    ]
    return canvas.create_polygon(pts, smooth=True, **kw)


class Pill(tk.Canvas):
    """圆角按钮。

    三件事固定在这儿，免得每个调用点各写一遍还写错：

    1. **前景色自动兜底**。不给 `fg` 就按底色亮度选深色/白色 ——
       之前「浅灰底 + 默认白字」的按钮整片看不见。
    2. **抗锯齿**。用 `render_round_rect` 渲染成图，不用 Canvas 多边形。
    3. **尺寸按 DPI 放大**。字号是 point（Tk 自己按 DPI 放大），
       按钮高度不跟着放大，字就会顶满整颗按钮。宽高比一起放大才协调。

    `width` 例外：传进来的是像素（调用方按容器宽度算好的），不再放大。
    """

    VARIANTS = ("primary", "secondary", "ghost", "danger")

    def __init__(
        self,
        master,
        text: str,
        command=None,
        *,
        variant: str = "primary",
        width: int | None = None,
        height: int = 34,
        radius: int | None = None,
        fill: str | None = None,
        fill_hover: str | None = None,
        fg: str | None = None,
        outline: str = "",
        font=None,
        padx: int = 18,
        bg: str | None = None,
    ) -> None:
        font = font or theme.FONT_SM
        self._font = tkfont.Font(font=font)
        self._text = text
        self._command = command
        self._enabled = True
        self._bg = hex_color(master, bg or master.cget("bg"))

        scale = max(1.0, theme.DPI_SCALE)
        self._ph = max(round(height * scale), self._font.metrics("linespace") + round(12 * scale))
        pad = round(padx * scale)
        self._pw = round(width) if width else self._font.measure(text) + pad * 2
        self._radius = round((radius if radius is not None else height / 2) * scale)

        fill, hover, fg, outline = self._resolve(variant, fill, fill_hover, fg, outline, self._bg)
        self._apply_colors(fill, hover, fg, outline)

        super().__init__(
            master, width=self._pw, height=self._ph, bg=self._bg, highlightthickness=0, bd=0
        )
        self._item = self.create_image(0, 0, anchor="nw")
        self._label = self.create_text(
            self._pw / 2, self._ph / 2 + 1, text=text, fill=self._fg, font=font
        )
        self._paint()
        self.configure(cursor="hand2")
        self.bind("<Button-1>", self._on_click)
        self.bind("<Enter>", self._on_enter)
        self.bind("<Leave>", self._on_leave)

    # ---------------------------------------------------------- 颜色

    @staticmethod
    def _resolve(variant: str, fill, fill_hover, fg, outline, bg: str = ""):
        """没显式给颜色的部分按 variant 取默认值。"""
        if variant == "ghost":
            # 透明按钮：底色跟容器一致，悬停用一点浅灰
            return (
                fill or bg or theme.BG_CARD,
                fill_hover or theme.BG_CARD,
                fg or theme.TEXT_MUTED,
                outline,
            )

        preset = {
            "primary": (theme.ACCENT, theme.ACCENT_HOVER, None, ""),
            "secondary": (theme.BG_CARD, theme.SIDEBAR_HOVER, None, theme.BORDER_STRONG),
            "danger": (theme.DANGER_SOFT, theme.DANGER_SOFT, theme.DANGER, ""),
        }.get(variant, (theme.ACCENT, theme.ACCENT_HOVER, None, ""))

        base_fill, base_hover, base_fg, base_outline = preset
        fill = fill or base_fill or theme.ACCENT
        hover = fill_hover or base_hover or fill
        outline = outline if outline != "" else base_outline
        fg = fg or base_fg or auto_fg(fill)
        return fill, hover, fg, outline

    def _apply_colors(self, fill, hover, fg, outline) -> None:
        self._fill = fill
        self._fill_hover = hover
        self._fg = fg
        self._outline = outline

    # ---------------------------------------------------------- 绘制

    def _paint(self) -> None:
        self._img = render_round_rect(
            self._pw, self._ph, self._radius, self._fill, self._bg, self._outline
        )
        self._img_hover = render_round_rect(
            self._pw, self._ph, self._radius, self._fill_hover, self._bg, self._outline
        )
        self.itemconfig(self._item, image=self._img)
        self.itemconfig(self._label, fill=self._fg)

    def _on_click(self, _event) -> None:
        if self._enabled and self._command:
            self._command()

    def _on_enter(self, _event) -> None:
        if self._enabled:
            self.itemconfig(self._item, image=self._img_hover)

    def _on_leave(self, _event) -> None:
        self.itemconfig(self._item, image=self._img)

    # ---------------------------------------------------------- 外部改状态

    def set_enabled(self, enabled: bool, *, fill: str | None = None, fg: str | None = None) -> None:
        self._enabled = enabled
        if fill or fg:
            self._apply_colors(
                fill or self._fill,
                fill or self._fill_hover,
                fg or self._fg,
                self._outline,
            )
            self._paint()
        self.configure(cursor="hand2" if enabled else "arrow")

    def set_style(self, *, variant: str | None = None, fill: str | None = None,
                  fg: str | None = None, outline: str | None = None) -> None:
        """换一套配色（分类筛选那种「选中态」用）。"""
        if variant:
            fill, hover, fg, outline_ = self._resolve(
                variant, fill, None, fg, outline or "", self._bg
            )
            self._apply_colors(fill, hover, fg, outline_)
        else:
            self._apply_colors(
                fill or self._fill,
                fill or self._fill_hover,
                fg or self._fg,
                self._outline if outline is None else outline,
            )
        self._paint()

    def set_size(self, width: int | None = None, height: int | None = None) -> None:
        """容器宽度变了要重画：图是固定尺寸的，pack(fill="x") 拉不动它。"""
        if width:
            self._pw = max(8, round(width))
        if height:
            self._ph = max(8, round(height))
        self.configure(width=self._pw, height=self._ph)
        self.coords(self._label, self._pw / 2, self._ph / 2 + 1)
        self._paint()

    def set_text(self, text: str) -> None:
        self._text = text
        self.itemconfig(self._label, text=text)


class IconButton(tk.Canvas):
    """小图标按钮：图标是渲染出来的图，悬停加一层浅底。"""

    def __init__(
        self,
        master,
        command=None,
        *,
        kind: str = "sidebar",
        size: int = 28,
        bg: str | None = None,
        fg: str | None = None,
    ) -> None:
        parent_bg = hex_color(master, bg or master.cget("bg"))
        px = round(size * max(1.0, theme.DPI_SCALE))
        super().__init__(
            master, width=px, height=px, bg=parent_bg, highlightthickness=0, bd=0, cursor="hand2"
        )
        self._command = command
        self._px = px
        color = fg or theme.TEXT_MUTED

        if kind == "sidebar":
            maker = render_sidebar_toggle
        else:  # 兜底：只有这一种图形，加新图标在这里扩展
            maker = render_sidebar_toggle

        self._icon = maker(px, px, color, parent_bg)
        self._hover_icon = maker(px, px, theme.TEXT, theme.SIDEBAR_HOVER)
        self._pad = self.create_image(-px * 2, 0, anchor="nw", image=self._hover_icon)
        self._icon_item = self.create_image(0, 0, anchor="nw", image=self._icon)

        self.bind("<Button-1>", lambda e: self._command and self._command())
        self.bind("<Enter>", lambda e: self.coords(self._pad, 0, 0))
        self.bind("<Leave>", lambda e: self.coords(self._pad, -px * 2, 0))

    def set_command(self, command) -> None:
        self._command = command


class Toggle(tk.Canvas):
    """开关：一张预渲染的抗锯齿图，开和关各一张。

    轨道和圆点画在同一张图上 —— 拆成两个图形的话，圆点边缘还是会硬。
    """

    W, H = 38, 21

    def __init__(self, master, variable: tk.BooleanVar, command=None, bg: str | None = None) -> None:
        bg = hex_color(master, bg or theme.BG)
        scale = theme.DPI_SCALE
        self._pw, self._ph = round(self.W * scale), round(self.H * scale)
        super().__init__(
            master, width=self._pw, height=self._ph, bg=bg, highlightthickness=0, bd=0, cursor="hand2"
        )
        self.var = variable
        self._command = command
        # 两张图都要留住引用，否则被 GC 掉就变成白块
        self._img_on = render_toggle(self._pw, self._ph, True, bg)
        self._img_off = render_toggle(self._pw, self._ph, False, bg)
        self._item = self.create_image(0, 0, image=self._img_off, anchor="nw")
        self.bind("<Button-1>", self._toggle)
        self._redraw()

    def _redraw(self) -> None:
        self.itemconfig(self._item, image=self._img_on if self.var.get() else self._img_off)

    def _toggle(self, _event) -> None:
        self.var.set(not self.var.get())
        self._redraw()
        if self._command:
            self._command()


class Divider(tk.Frame):
    """1px 细线。"""

    def __init__(self, master, *, color: str | None = None, **kw) -> None:
        super().__init__(master, bg=color or theme.BORDER, height=1, **kw)


class CircleButton(tk.Canvas):
    """圆形按钮，用于发送。"""

    def __init__(
        self,
        master,
        glyph: str = "↑",
        command=None,
        *,
        size: int = 34,
        fill: str | None = None,
        hover: str | None = None,
        fg: str | None = None,
        bg: str | None = None,
        font=None,
    ) -> None:
        fill = fill or theme.INK
        hover = hover or theme.INK_HOVER
        fg = fg or theme.ON_ACCENT
        font = font or (theme.FONT_BODY[0], round(size * 0.42), "bold")
        parent_bg = hex_color(master, bg or master.cget("bg"))

        px = round(size * theme.DPI_SCALE)
        super().__init__(master, width=px, height=px, bg=parent_bg, highlightthickness=0, bd=0)
        self._px = px
        self._bg = parent_bg
        self._fill, self._hover = fill, hover
        self._enabled = True
        self._command = command
        # 圆的边缘靠距离场抗锯齿，见文件头的说明
        self._img_fill = render_round_rect(px, px, px / 2, fill, parent_bg)
        self._img_hover = render_round_rect(px, px, px / 2, hover, parent_bg)
        self._item = self.create_image(0, 0, image=self._img_fill, anchor="nw")
        self._glyph = self.create_text(px / 2, px / 2 + 1, text=glyph, fill=fg, font=font)
        self.configure(cursor="hand2")
        self.bind("<Button-1>", lambda e: self._enabled and self._command and self._command())
        self.bind("<Enter>", lambda e: self._enabled and self.itemconfig(self._item, image=self._img_hover))
        self.bind("<Leave>", lambda e: self.itemconfig(self._item, image=self._img_fill))

    def set_enabled(self, enabled: bool, *, fill: str | None = None) -> None:
        self._enabled = enabled
        if fill:
            self._fill = fill
            self._img_fill = render_round_rect(self._px, self._px, self._px / 2, fill, self._bg)
            self.itemconfig(self._item, image=self._img_fill)
        self.configure(cursor="hand2" if enabled else "arrow")


class ScrollFrame(tk.Frame):
    """可滚动的容器。

    Tk 没有现成的，只能 Canvas 套 Frame。滚轮用 bind_all 挂上、鼠标离开就摘掉，
    否则会抢走主窗口其他区域的滚动。
    """

    def __init__(self, master, *, bg: str | None = None) -> None:
        bg = bg or theme.BG
        super().__init__(master, bg=bg)
        self.canvas = tk.Canvas(self, bg=bg, highlightthickness=0, bd=0)
        self.bar = tk.Scrollbar(self, orient="vertical", command=self.canvas.yview, width=10)
        self.canvas.configure(yscrollcommand=self._on_scroll)
        self.canvas.pack(side="left", fill="both", expand=True)
        self.bar.pack(side="right", fill="y")

        # 内容都往 body 里放
        self.body = tk.Frame(self.canvas, bg=bg)
        self._window = self.canvas.create_window((0, 0), window=self.body, anchor="nw")

        self.body.bind("<Configure>", self._sync_height)
        self.canvas.bind("<Configure>", self._sync_width)
        self.bind("<Enter>", self._grab_wheel)
        self.bind("<Leave>", self._release_wheel)

    def _sync_height(self, _event=None) -> None:
        self.canvas.configure(scrollregion=self.canvas.bbox("all"))

    def _sync_width(self, event) -> None:
        # 内容跟着容器宽度走，不然换字号后右侧会被切
        self.canvas.itemconfigure(self._window, width=event.width)

    def _on_scroll(self, first: str, last: str) -> None:
        # 装得下就把滚动条藏起来，界面上少一条灰边
        if float(first) <= 0.0 and float(last) >= 1.0:
            self.bar.pack_forget()
        else:
            self.bar.pack(side="right", fill="y")
        self.bar.set(first, last)

    def _grab_wheel(self, _event=None) -> None:
        self.canvas.bind_all("<MouseWheel>", self._on_wheel)
        self.canvas.bind_all("<Button-4>", self._on_wheel)
        self.canvas.bind_all("<Button-5>", self._on_wheel)

    def _release_wheel(self, _event=None) -> None:
        self.canvas.unbind_all("<MouseWheel>")
        self.canvas.unbind_all("<Button-4>")
        self.canvas.unbind_all("<Button-5>")

    def _on_wheel(self, event) -> None:
        if getattr(event, "num", None) == 4:
            step = -3
        elif getattr(event, "num", None) == 5:
            step = 3
        else:
            step = -3 if getattr(event, "delta", 0) > 0 else 3
        self.canvas.yview_scroll(step, "units")


class Avatar(tk.Canvas):
    """圆形头像。

    有图就用图，没图退化成「一个圆 + 一个字」。

    Tk 不能裁剪图片，所以圆形是逐像素刷出来的：把圆外的格子设成透明。
    42x42 也就一千多格，一次性的开销可以忽略。
    """

    def __init__(
        self,
        master,
        size: int = 42,
        text: str = "我",
        *,
        image_path=None,
        fill: str | None = None,
        fg: str | None = None,
        on_click=None,
        bg: str | None = None,
    ) -> None:
        parent_bg = hex_color(master, bg or master.cget("bg"))
        super().__init__(master, width=size, height=size, bg=parent_bg, highlightthickness=0, bd=0)
        fill = fill or theme.ACCENT
        fg = fg or theme.ON_ACCENT
        half = size / 2

        self._photo = None
        if image_path:
            self._photo = self._round_photo(image_path, size)

        if self._photo is not None:
            self.create_image(half, half, image=self._photo)
            self._ring = self.create_oval(
                0.5, 0.5, size - 0.5, size - 0.5, outline=theme.BORDER_STRONG, width=1
            )
        else:
            self._ring = self.create_oval(1, 1, size - 1, size - 1, fill=fill, outline=fill)
            self.create_text(
                half, half + 1, text=text, fill=fg,
                font=(theme.FONT_SM[0], max(9, round(size * 0.42)), "bold"),
            )

        if on_click:
            self.configure(cursor="hand2")
            self.bind("<Button-1>", lambda e: on_click(self))
            self.bind("<Enter>", lambda e: self.itemconfig(self._ring, outline=theme.ACCENT))
            self.bind(
                "<Leave>",
                lambda e: self.itemconfig(
                    self._ring, outline=theme.BORDER_STRONG if self._photo else fill
                ),
            )

    @staticmethod
    def _round_photo(path, size: int):
        """缩到目标尺寸并把圆外刷成透明。失败返回 None，调用方退回文字头像。"""
        return round_photo(path, size, radius=None)


class NavItem(tk.Frame):
    """侧栏条目：图标 + 文字，悬停变色。"""

    def __init__(self, master, glyph: str, text: str, command=None, *, bg: str | None = None) -> None:
        bg = bg or theme.SIDEBAR
        super().__init__(master, bg=bg, cursor="hand2")
        self._bg = bg
        self._command = command
        self._glyph = tk.Label(self, text=glyph, bg=bg, fg=theme.TEXT_MUTED, font=theme.FONT_SM, width=2)
        self._glyph.pack(side="left", padx=(8, 2), pady=5)
        self._label = tk.Label(self, text=text, bg=bg, fg=theme.TEXT, font=theme.FONT_SM, anchor="w")
        self._label.pack(side="left", pady=5)
        for widget in (self, self._glyph, self._label):
            widget.bind("<Button-1>", self._click)
            widget.bind("<Enter>", self._enter)
            widget.bind("<Leave>", self._leave)

    def _click(self, _event) -> None:
        if self._command:
            self._command()

    def _enter(self, _event) -> None:
        for widget in (self, self._glyph, self._label):
            widget.configure(bg=theme.SIDEBAR_HOVER)

    def _leave(self, _event) -> None:
        for widget in (self, self._glyph, self._label):
            widget.configure(bg=self._bg)
