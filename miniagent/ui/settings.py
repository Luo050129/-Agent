"""交互层：界面偏好设置。

存 `.agent/settings.json`，只管外观（主题、字体、字号）。
模型和插件的配置不在这儿：模型在 `model/registry.py` + `models.json`，插件在 `plugins/`。
"""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass
from pathlib import Path

from ..config import AGENT_DIR

SETTINGS_FILE = AGENT_DIR / "settings.json"

THEMES = ("light", "dark", "warm")
THEME_LABELS = {"light": "浅色", "dark": "深色", "warm": "米色"}
MIN_SCALE, MAX_SCALE = 0.85, 1.6


@dataclass
class UISettings:
    theme: str = "light"
    font_family: str = ""  # 空 = 自动挑一个系统中可用的
    font_scale: float = 1.0

    def normalized(self) -> "UISettings":
        """把越界值拉回合法范围，坏配置不该让界面起不来。"""
        theme = self.theme if self.theme in THEMES else "light"
        try:
            scale = float(self.font_scale)
        except (TypeError, ValueError):
            scale = 1.0
        scale = max(MIN_SCALE, min(MAX_SCALE, scale))
        return UISettings(theme=theme, font_family=(self.font_family or "").strip(), font_scale=round(scale, 2))


def load(path: Path | None = None) -> UISettings:
    target = path or SETTINGS_FILE
    if not target.is_file():
        return UISettings()
    try:
        data = json.loads(target.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return UISettings()
    if not isinstance(data, dict):
        return UISettings()
    return UISettings(
        theme=str(data.get("theme") or "light"),
        font_family=str(data.get("font_family") or ""),
        font_scale=data.get("font_scale", 1.0),
    ).normalized()


def save(settings: UISettings, path: Path | None = None) -> bool:
    target = path or SETTINGS_FILE
    try:
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(
            json.dumps(asdict(settings.normalized()), ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
        return True
    except OSError:
        return False
