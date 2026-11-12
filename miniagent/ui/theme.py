"""交互层：设计系统。

三套调色板 + 可配字体，界面所有颜色和字号都从这里取。

用法：`theme.apply_theme("dark")` / `theme.apply_fonts("Microsoft YaHei UI", 1.1)`。
**改完必须重建窗口** —— Tk 控件在构造时就把颜色和字体定死了，改模块变量不会回写到已存在的控件。
"""

from __future__ import annotations

import platform

# ---------------------------------------------------------------- 调色板

LIGHT = {
    "BG": "#FFFFFF",
    "BG_SOFT": "#FAFAFA",
    "BG_CARD": "#F6F6F7",
    "BORDER": "#EDEDED",
    "BORDER_STRONG": "#E0E0E2",
    "SIDEBAR": "#F7F7F8",
    "SIDEBAR_HOVER": "#ECECEE",
    "SIDEBAR_ACTIVE": "#E6E6E9",
    "INK": "#18181B",
    "INK_HOVER": "#3F3F46",
    "TEXT": "#111111",
    "TEXT_BODY": "#1F1F23",
    "TEXT_MUTED": "#6B6B72",
    "TEXT_FAINT": "#9CA3AF",
    "TEXT_GHOST": "#C4C4CC",
    "ACCENT": "#FF6A13",
    "ACCENT_HOVER": "#EE5B04",
    "ACCENT_SOFT": "#FFF4EC",
    "ACCENT_DEEP": "#C2410C",
    "DANGER": "#DC2626",
    "DANGER_SOFT": "#FEF2F2",
    "SUCCESS": "#16A34A",
    "NOTICE": "#B45309",
    "ON_ACCENT": "#FFFFFF",
}

DARK = {
    "BG": "#17171A",
    "BG_SOFT": "#1C1C20",
    "BG_CARD": "#212126",
    "BORDER": "#2A2A30",
    "BORDER_STRONG": "#3A3A42",
    "SIDEBAR": "#121215",
    "SIDEBAR_HOVER": "#1E1E23",
    "SIDEBAR_ACTIVE": "#28282F",
    "INK": "#F2F2F4",
    "INK_HOVER": "#D4D4D8",
    "TEXT": "#F2F2F4",
    "TEXT_BODY": "#DCDCDF",
    "TEXT_MUTED": "#9A9AA4",
    "TEXT_FAINT": "#75757F",
    "TEXT_GHOST": "#55555F",
    "ACCENT": "#FF7A2F",
    "ACCENT_HOVER": "#FF8F4D",
    "ACCENT_SOFT": "#2B1A10",
    "ACCENT_DEEP": "#FFAE73",
    "DANGER": "#F87171",
    "DANGER_SOFT": "#2A1517",
    "SUCCESS": "#4ADE80",
    "NOTICE": "#FBBF24",
    "ON_ACCENT": "#FFFFFF",
}

WARM = {
    "BG": "#FBF9F4",
    "BG_SOFT": "#F6F3EA",
    "BG_CARD": "#F0ECE1",
    "BORDER": "#E5DFD1",
    "BORDER_STRONG": "#D5CDBA",
    "SIDEBAR": "#F4F1E8",
    "SIDEBAR_HOVER": "#EBE6D9",
    "SIDEBAR_ACTIVE": "#E1DACA",
    "INK": "#2A2823",
    "INK_HOVER": "#45413A",
    "TEXT": "#1E1C18",
    "TEXT_BODY": "#2E2B25",
    "TEXT_MUTED": "#6D675C",
    "TEXT_FAINT": "#97907F",
    "TEXT_GHOST": "#BFB9A8",
    "ACCENT": "#2F6F4E",
    "ACCENT_HOVER": "#255A3F",
    "ACCENT_SOFT": "#E7F0E9",
    "ACCENT_DEEP": "#1D5238",
    "DANGER": "#B3261E",
    "DANGER_SOFT": "#FBEDEB",
    "SUCCESS": "#2F6F4E",
    "NOTICE": "#8A5A00",
    "ON_ACCENT": "#FFFFFF",
}

PALETTES = {"light": LIGHT, "dark": DARK, "warm": WARM}

# ---------------------------------------------------------------- 字体

_SYSTEM = platform.system()

# 按优先级探测，取系统里第一个存在的。
# Noto Sans SC（思源黑体的 Google 版）字形比雅黑规整、字重均匀，和粉色系图标更搭。
SANS_CANDIDATES = {
    "Windows": ["Noto Sans SC", "Source Han Sans SC", "Microsoft YaHei UI", "Microsoft YaHei", "Segoe UI"],
    "Darwin": ["PingFang SC", "Helvetica Neue", "Helvetica"],
}.get(_SYSTEM, ["Noto Sans CJK SC", "WenQuanYi Micro Hei", "DejaVu Sans"])

MONO_CANDIDATES = {
    "Windows": ["JetBrains Mono", "Cascadia Code", "Cascadia Mono", "Consolas", "Courier New"],
    "Darwin": ["SF Mono", "Menlo", "Monaco"],
}.get(_SYSTEM, ["JetBrains Mono", "DejaVu Sans Mono", "Liberation Mono", "Courier New"])

# 基准字号（1.0 倍时）。之前 body 10 / sm 9 太小，整体上调一档。
_SIZES = {
    "FONT_BRAND": 13,
    "FONT_HERO": 21,
    "FONT_H": 12,
    "FONT_BODY": 11,
    "FONT_BODY_BOLD": 11,
    "FONT_SM": 10,
    "FONT_STAT_NUM": 13,
    "FONT_LOGO": 14,
    "FONT_MONO": 10,
}

_family_sans = SANS_CANDIDATES[0]
_family_mono = MONO_CANDIDATES[0]
_scale = 1.0

# 这些名字在下面由 apply_fonts 填充，模块级变量保证 `theme.FONT_BODY` 这种用法不变
FONT_BRAND: tuple = ()
FONT_HERO: tuple = ()
FONT_H: tuple = ()
FONT_BODY: tuple = ()
FONT_BODY_BOLD: tuple = ()
FONT_SM: tuple = ()
FONT_STAT_NUM: tuple = ()
FONT_LOGO: tuple = ()
FONT_MONO: tuple = ()


def pick_family(available: set[str], candidates: list[str] | None = None) -> str:
    """从系统实际可用的字体里挑第一个。都不在就返回候选表首项（Tk 会回退）。"""
    wanted = candidates or SANS_CANDIDATES
    lowered = {name.lower() for name in available}
    for name in wanted:
        if name.lower() in lowered:
            return name
    return wanted[0]


def apply_fonts(family: str | None = None, scale: float | None = None, mono: str | None = None) -> None:
    """重算所有字号元组。family 为空则保持当前值。"""
    global _family_sans, _family_mono, _scale
    global FONT_BRAND, FONT_HERO, FONT_H, FONT_BODY, FONT_BODY_BOLD
    global FONT_SM, FONT_STAT_NUM, FONT_LOGO, FONT_MONO

    if family:
        _family_sans = family
    if mono:
        _family_mono = mono
    if scale is not None:
        _scale = max(0.85, min(1.6, float(scale)))

    def size(key: str) -> int:
        return max(8, round(_SIZES[key] * _scale))

    FONT_BRAND = (_family_sans, size("FONT_BRAND"), "bold")
    FONT_HERO = (_family_sans, size("FONT_HERO"), "bold")
    FONT_H = (_family_sans, size("FONT_H"), "bold")
    FONT_BODY = (_family_sans, size("FONT_BODY"))
    FONT_BODY_BOLD = (_family_sans, size("FONT_BODY_BOLD"), "bold")
    FONT_SM = (_family_sans, size("FONT_SM"))
    FONT_STAT_NUM = (_family_sans, size("FONT_STAT_NUM"), "bold")
    FONT_LOGO = (_family_sans, size("FONT_LOGO"), "bold")
    FONT_MONO = (_family_mono, size("FONT_MONO"))


def apply_theme(name: str) -> str:
    """切换调色板。返回实际生效的名字（非法值回落到 light）。"""
    palette_name = name if name in PALETTES else "light"
    globals().update(PALETTES[palette_name])
    globals()["THEME_NAME"] = palette_name
    return palette_name


THEME_NAME = "light"

# 当前屏幕的缩放系数（1.0 = 96dpi）。Canvas 自绘的图形要按它放大，
# 不然高 DPI 下还是 38px 的圆，边缘全是硬台阶。
DPI_SCALE = 1.0


def set_dpi_scale(value: float) -> None:
    global DPI_SCALE
    DPI_SCALE = max(1.0, float(value))


def scale_px(value: int) -> int:
    """逻辑尺寸 → 当前 DPI 下的像素。"""
    return max(1, round(value * max(1.0, DPI_SCALE)))


def dialog_geometry(widget, width: int, height: int) -> str:
    """弹窗尺寸也要按 DPI 放大，否则 150% 缩放下字会把窗口撑爆。

    主窗口有 apply_geometry 干这事，弹窗各写各的容易漏 —— 统一走这里。
    """
    factor = max(1.0, DPI_SCALE)
    try:
        screen_w = widget.winfo_screenwidth()
        screen_h = widget.winfo_screenheight()
    except Exception:  # noqa: BLE001
        return f"{width}x{height}"
    w = min(round(width * factor), int(screen_w * 0.92))
    h = min(round(height * factor), int(screen_h * 0.88))
    return f"{max(560, w)}x{max(420, h)}"

# ---------------------------------------------------------------- 高 DPI

def enable_dpi_awareness() -> bool:
    """Windows 上声明 DPI 感知。

    不做这步，高缩放比下 Tk 画出来的位图会被系统整体拉伸，字全发虚 ——
    换字体字号都救不回来，这才是「看着不清晰」的根因。
    """
    if _SYSTEM != "Windows":
        return False
    try:
        import ctypes

        # 2 = PER_MONITOR_DPI_AWARE，Win8.1+
        ctypes.windll.shcore.SetProcessDpiAwareness(2)
        return True
    except Exception:  # noqa: BLE001 老系统没有 shcore
        try:
            import ctypes

            ctypes.windll.user32.SetProcessDPIAware()
            return True
        except Exception:  # noqa: BLE001
            return False


# 初始化一次，保证 import 之后就有值可用
apply_theme("light")
apply_fonts()
