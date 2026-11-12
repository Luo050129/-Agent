"""主题与字体测试：三套配色、字号缩放、DPI、非法值兜底。

`python tests/test_theme.py` 直接跑。
"""

from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from miniagent.ui import theme  # noqa: E402

KEYS = (
    "BG", "BG_SOFT", "BG_CARD", "BORDER", "BORDER_STRONG",
    "SIDEBAR", "SIDEBAR_HOVER", "SIDEBAR_ACTIVE",
    "INK", "INK_HOVER",
    "TEXT", "TEXT_BODY", "TEXT_MUTED", "TEXT_FAINT", "TEXT_GHOST",
    "ACCENT", "ACCENT_HOVER", "ACCENT_SOFT", "ACCENT_DEEP",
    "DANGER", "DANGER_SOFT", "SUCCESS", "NOTICE", "ON_ACCENT",
)


def _luminance(hex_color: str) -> float:
    value = hex_color.lstrip("#")
    r, g, b = (int(value[i : i + 2], 16) / 255 for i in (0, 2, 4))
    return 0.2126 * r + 0.7152 * g + 0.0722 * b


def test_palettes_complete() -> list[tuple[str, bool]]:
    checks = []
    checks.append(("三套配色都在", set(theme.PALETTES) == {"light", "dark", "warm"}))
    for name, palette in theme.PALETTES.items():
        missing = [k for k in KEYS if k not in palette]
        checks.append((f"{name} 字段齐全", not missing))
        checks.append((f"{name} 全是 #RRGGBB", all(
            isinstance(v, str) and len(v) == 7 and v.startswith("#") for v in palette.values()
        )))
    return checks


def test_contrast() -> list[tuple[str, bool]]:
    """正文和背景得能看清，这是换主题最容易翻车的地方。"""
    checks = []
    for name, palette in theme.PALETTES.items():
        body = _luminance(palette["TEXT"])
        bg = _luminance(palette["BG"])
        gap = abs(body - bg)
        checks.append((f"{name} 正文对比足够（{gap:.2f}）", gap > 0.5))
        muted = abs(_luminance(palette["TEXT_MUTED"]) - bg)
        checks.append((f"{name} 次要文字也看得见（{muted:.2f}）", muted > 0.25))
    return checks


def test_apply_theme() -> list[tuple[str, bool]]:
    original = theme.THEME_NAME
    light_bg = theme.PALETTES["light"]["BG"]

    theme.apply_theme("dark")
    dark_ok = theme.BG == theme.PALETTES["dark"]["BG"] and theme.BG != light_bg

    theme.apply_theme("warm")
    warm_ok = theme.BG == theme.PALETTES["warm"]["BG"]

    theme.apply_theme("不存在的主题")
    fallback_ok = theme.BG == light_bg and theme.THEME_NAME == "light"

    theme.apply_theme(original)
    return [
        ("切到深色，BG 跟着变", dark_ok),
        ("切到米色，BG 跟着变", warm_ok),
        ("非法主题回落到浅色", fallback_ok),
        ("切回原主题恢复正常", theme.BG == theme.PALETTES[original]["BG"]),
    ]


def test_fonts() -> list[tuple[str, bool]]:
    theme.apply_fonts(scale=1.0)
    base_body = theme.FONT_BODY[1]
    base_sm = theme.FONT_SM[1]

    theme.apply_fonts(scale=1.3)
    big_body = theme.FONT_BODY[1]

    theme.apply_fonts(scale=0.5)  # 越界要夹紧
    tiny_body = theme.FONT_BODY[1]

    theme.apply_fonts(family="测试字体", scale=1.0)
    family_ok = theme.FONT_BODY[0] == "测试字体"

    theme.apply_fonts(family="Microsoft YaHei UI", scale=1.0)
    return [
        ("字号随倍数变大", big_body > base_body),
        ("越界倍数被夹紧", tiny_body >= 8),
        ("字号永远是整数", all(isinstance(f[1], int) for f in (
            theme.FONT_BODY, theme.FONT_SM, theme.FONT_H, theme.FONT_HERO,
        ))),
        ("字体族可以换", family_ok),
        ("字号整体被上调过（body >= 11）", base_body >= 11),
        ("小字也没低于 10", base_sm >= 10),
        ("等宽字体保留", len(theme.FONT_MONO) >= 2),
    ]


def test_pick_family() -> list[tuple[str, bool]]:
    available = {"Arial", "Segoe UI", "Consolas"}
    picked = theme.pick_family(available, ["不存在的字体", "Segoe UI", "Arial"])
    none_hit = theme.pick_family({"别的字体"}, ["也不存在", "Arial"])
    return [
        ("挑到第一个可用的", picked == "Segoe UI"),
        ("都不在时回落到首项", none_hit == "也不存在"),
        ("大小写不敏感", theme.pick_family({"segoe ui"}, ["Segoe UI"]) == "Segoe UI"),
    ]


def test_dpi() -> list[tuple[str, bool]]:
    result = theme.enable_dpi_awareness()
    return [
        ("DPI 设置不抛异常", isinstance(result, bool)),
        ("非 Windows 或已设置都能返回布尔", result in (True, False)),
    ]


def main() -> int:
    print("[theme] 开始")
    checks = (
        test_palettes_complete()
        + test_contrast()
        + test_apply_theme()
        + test_fonts()
        + test_pick_family()
        + test_dpi()
    )
    for label, ok in checks:
        print(f"  {'PASS' if ok else 'FAIL'}  {label}")
    ok_all = all(ok for _, ok in checks)
    print(f"[theme] {'全部通过' if ok_all else '存在失败项'}")
    return 0 if ok_all else 1


if __name__ == "__main__":
    raise SystemExit(main())
