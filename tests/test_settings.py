"""设置与自定义模型测试：持久化、越界兜底、增删改。

`python tests/test_settings.py` 直接跑。
"""

from __future__ import annotations

import json
import os
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from miniagent.model import custom  # noqa: E402
from miniagent.model.registry import all_specs, find, mainstream_specs  # noqa: E402
from miniagent.ui import settings  # noqa: E402


def test_settings_roundtrip() -> list[tuple[str, bool]]:
    tmp = Path(tempfile.mkdtemp()) / "settings.json"

    default = settings.load(tmp)
    checks = [
        ("没有文件时给默认值", default.theme == "light" and default.font_scale == 1.0),
    ]

    saved = settings.UISettings(theme="dark", font_family="Consolas", font_scale=1.2)
    checks.append(("保存成功", settings.save(saved, tmp)))
    loaded = settings.load(tmp)
    checks.append(("读回来一致", loaded.theme == "dark" and loaded.font_scale == 1.2))
    checks.append(("字体族也记住了", loaded.font_family == "Consolas"))

    tmp.write_text("{ 这不是 json", encoding="utf-8")
    checks.append(("坏文件回退默认，不炸", settings.load(tmp).theme == "light"))

    tmp.write_text(json.dumps({"theme": "紫色", "font_scale": 99}), encoding="utf-8")
    clamped = settings.load(tmp)
    checks.append(("非法主题回落", clamped.theme == "light"))
    checks.append(("越界字号被夹紧", settings.MIN_SCALE <= clamped.font_scale <= settings.MAX_SCALE))

    return checks


def test_custom_models() -> list[tuple[str, bool]]:
    tmp = Path(tempfile.mkdtemp()) / "models.json"

    spec = custom.build(
        key="本地 Qwen",
        provider="本地",
        protocol="openai",
        model="qwen2.5:14b",
        base_url="http://127.0.0.1:11434/v1",
    )
    checks = [
        ("能拼出 spec", spec is not None),
        ("缺环境变量时自动生成", spec is not None and spec.key_env and spec.key_env[0].endswith("_API_KEY")),
    ]
    if spec is None:
        return checks

    ok, message = custom.add(spec, tmp)
    checks.append(("添加成功", ok and "已添加" in message))
    checks.append(("能读回来", any(s.key == "本地 Qwen" for s in custom.load(tmp))))

    dup, message = custom.add(spec, tmp)
    checks.append(("重复添加被拒", not dup and "已经有" in message))

    builtin = custom.build(
        key="DeepSeek V4 Flash", provider="x", protocol="openai",
        model="m", base_url="http://x/v1",
    )
    if builtin:
        ok2, message2 = custom.add(builtin, tmp)
        checks.append(("与内置重名被拒", not ok2 and "内置" in message2))

    bad = custom.build(key="", provider="x", protocol="openai", model="m", base_url="http://x/v1")
    checks.append(("空显示名不让建", bad is None))

    checks.append(("删除成功", custom.remove("本地 Qwen", tmp)))
    checks.append(("删完就没了", not custom.load(tmp)))
    checks.append(("删不存在的不报错", not custom.remove("根本没有", tmp)))

    # 坏文件不影响其他功能
    tmp.write_text("not json", encoding="utf-8")
    checks.append(("坏文件返回空列表", custom.load(tmp) == []))
    return checks


def test_custom_in_catalog() -> list[tuple[str, bool]]:
    """自定义模型要能被 find 和主流列表看到。"""
    original = custom.CUSTOM_FILE
    tmp_dir = Path(tempfile.mkdtemp())
    custom.CUSTOM_FILE = tmp_dir / "models.json"
    custom._cache["stamp"] = None

    try:
        spec = custom.build(
            key="自建模型", provider="自建", protocol="openai",
            model="my-model", base_url="http://127.0.0.1:9/v1",
        )
        assert spec is not None
        custom.add(spec)

        merged = all_specs()
        found = find("自建模型")
        mainstream_keys = {s.key for s in mainstream_specs()}

        return [
            ("自定义进 all_specs", any(s.key == "自建模型" for s in merged)),
            ("find 能找到自定义", found is not None and found.model == "my-model"),
            ("自定义排在内置前面", merged[0].key == "自建模型"),
            ("自定义也出现在主流列表", "自建模型" in mainstream_keys),
            ("内置模型没被挤掉", len(merged) > 40),
        ]
    finally:
        custom.CUSTOM_FILE = original
        custom._cache["stamp"] = None


def main() -> int:
    print("[settings] 开始")
    checks = test_settings_roundtrip() + test_custom_models() + test_custom_in_catalog()
    for label, ok in checks:
        print(f"  {'PASS' if ok else 'FAIL'}  {label}")
    ok_all = all(ok for _, ok in checks)
    print(f"[settings] {'全部通过' if ok_all else '存在失败项'}")
    return 0 if ok_all else 1


if __name__ == "__main__":
    raise SystemExit(main())
