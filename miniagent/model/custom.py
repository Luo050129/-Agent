"""模型层：用户自定义模型。

存 `.agent/models.json`，内容是 ModelSpec 的 JSON 数组。
界面里的「添加模型」和手改这个文件等效 —— 有 API 就能接，不用等内置目录更新。

例：
    [{"key": "本地 Qwen", "provider": "本地", "protocol": "openai",
      "model": "qwen2.5:14b", "base_url": "http://127.0.0.1:11434/v1",
      "key_env": "OLLAMA_API_KEY"}]
"""

from __future__ import annotations

import json
import re
from dataclasses import asdict
from pathlib import Path

from ..config import AGENT_DIR
from .registry import ModelSpec

CUSTOM_FILE = AGENT_DIR / "models.json"
VALID_PROTOCOLS = ("openai", "anthropic", "gemini", "responses")

_cache: dict = {"stamp": None, "specs": []}


def _to_spec(item: dict) -> ModelSpec | None:
    key = str(item.get("key") or "").strip()
    model = str(item.get("model") or "").strip()
    base_url = str(item.get("base_url") or "").strip()
    protocol = str(item.get("protocol") or "openai").strip().lower()
    if not key or not model or not base_url:
        return None
    if protocol not in VALID_PROTOCOLS:
        return None

    env = item.get("key_env") or ""
    if isinstance(env, list):
        envs = tuple(str(x).strip() for x in env if str(x).strip())
    else:
        envs = (str(env).strip(),) if str(env).strip() else ()
    if not envs:
        envs = (default_key_env(key),)

    try:
        context = int(item.get("context") or 128000)
    except (TypeError, ValueError):
        context = 128000

    quirks = item.get("quirks")
    return ModelSpec(
        key=key,
        provider=str(item.get("provider") or "自定义").strip() or "自定义",
        protocol=protocol,
        model=model,
        base_url=base_url,
        key_env=envs,
        quirks=dict(quirks) if isinstance(quirks, dict) else {},
        context=max(4096, context),
    )


def default_key_env(key: str) -> str:
    """没填环境变量名就按显示名生成一个，比如「本地 Qwen」→ LOCAL_QWEN_API_KEY。"""
    ascii_part = re.sub(r"[^A-Za-z0-9]+", "_", key).strip("_").upper()
    return f"{ascii_part or 'CUSTOM'}_API_KEY"


def load(path: Path | None = None) -> list[ModelSpec]:
    target = path or CUSTOM_FILE
    if not target.is_file():
        return []
    try:
        stat = target.stat()
    except OSError:
        return []

    stamp = (str(target), stat.st_mtime_ns, stat.st_size)
    if _cache["stamp"] == stamp:
        return list(_cache["specs"])

    try:
        data = json.loads(target.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return []

    rows = data if isinstance(data, list) else data.get("models", []) if isinstance(data, dict) else []
    specs = [spec for spec in (_to_spec(item) for item in rows if isinstance(item, dict)) if spec]

    _cache["stamp"] = stamp
    _cache["specs"] = specs
    return list(specs)


def save(specs: list[ModelSpec], path: Path | None = None) -> bool:
    target = path or CUSTOM_FILE
    try:
        target.parent.mkdir(parents=True, exist_ok=True)
        payload = [asdict(spec) for spec in specs]
        target.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        _cache["stamp"] = None  # 强制下次重读
        return True
    except OSError:
        return False


def add(spec: ModelSpec, path: Path | None = None) -> tuple[bool, str]:
    """加一个。返回 (成功, 说明)。重名会拒绝，免得菜单里两条一样的。"""
    if not spec.key.strip():
        return False, "显示名不能为空"
    if not spec.model.strip():
        return False, "模型 id 不能为空"
    if not spec.base_url.strip():
        return False, "接口地址不能为空"

    specs = load(path)
    if any(item.key == spec.key for item in specs):
        return False, f"已经有一个叫「{spec.key}」的自定义模型了"

    from .registry import CATALOG

    if any(item.key == spec.key for item in CATALOG):
        return False, f"内置目录里已经有「{spec.key}」，直接用它就行"

    specs.append(spec)
    if not save(specs, path):
        return False, "写 models.json 失败，检查目录权限"
    return True, f"已添加 {spec.key}"


def remove(key: str, path: Path | None = None) -> bool:
    specs = load(path)
    kept = [item for item in specs if item.key != key]
    if len(kept) == len(specs):
        return False
    return save(kept, path)


def build(
    key: str,
    provider: str,
    protocol: str,
    model: str,
    base_url: str,
    key_env: str = "",
    context: int = 128000,
) -> ModelSpec | None:
    """从界面字段拼一个 spec，字段不合法返回 None。"""
    return _to_spec(
        {
            "key": key,
            "provider": provider,
            "protocol": protocol,
            "model": model,
            "base_url": base_url,
            "key_env": key_env,
            "context": context,
        }
    )
