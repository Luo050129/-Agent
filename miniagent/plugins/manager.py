"""插件层：从 GitHub 装工具。

一个插件 = 一个目录，里面必须有 plugin.py，暴露两样东西：

    SCHEMAS: list[dict]          # OpenAI 工具定义
    HANDLERS: dict[str, callable]  # 函数名 → 实现

可选 META = {"name": ..., "version": ..., "description": ...}

装进来的插件默认是启用状态，注册进工具层后 Agent 立刻就能调。
插件是第三方代码，安装等于允许它在你的机器上执行 —— 界面会先让你确认。
"""

from __future__ import annotations

import importlib.util
import json
import shutil
import sys
from dataclasses import dataclass, field
from pathlib import Path

from .. import fetch
from ..config import BASE_DIR

PLUGIN_DIR = BASE_DIR / "plugins"
STATE_FILE = PLUGIN_DIR / "state.json"
META_FILE = "meta.json"
ENTRY_FILE = "plugin.py"


class PluginError(RuntimeError):
    """安装 / 加载插件失败。"""


@dataclass
class Plugin:
    name: str
    path: Path
    enabled: bool = True
    source: str = ""
    version: str = "—"
    description: str = ""
    tools: list[str] = field(default_factory=list)
    error: str = ""


# ---------------------------------------------------------------- 状态


def _state() -> dict:
    if not STATE_FILE.is_file():
        return {}
    try:
        return json.loads(STATE_FILE.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return {}


def _save_state(state: dict) -> None:
    PLUGIN_DIR.mkdir(parents=True, exist_ok=True)
    STATE_FILE.write_text(json.dumps(state, ensure_ascii=False, indent=2), encoding="utf-8")


def _set_enabled(name: str, enabled: bool) -> None:
    state = _state()
    state[name] = enabled
    _save_state(state)


def _is_enabled(name: str) -> bool:
    return _state().get(name, True)  # 新装的默认启用


# ---------------------------------------------------------------- 查询


def _read_meta(path: Path) -> dict:
    meta_file = path / META_FILE
    if meta_file.is_file():
        try:
            return json.loads(meta_file.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            return {}
    return {}


def list_plugins() -> list[Plugin]:
    """扫描插件目录，返回所有已安装插件（含加载错误）。"""
    if not PLUGIN_DIR.is_dir():
        return []

    result: list[Plugin] = []
    for item in sorted(PLUGIN_DIR.iterdir()):
        if not item.is_dir() or item.name.startswith(("_", ".")):
            continue
        meta = _read_meta(item)
        plugin = Plugin(
            name=item.name,
            path=item,
            enabled=_is_enabled(item.name),
            source=meta.get("source", ""),
            version=str(meta.get("version", "—")),
            description=meta.get("description", ""),
        )
        if not (item / ENTRY_FILE).is_file():
            plugin.error = f"缺少 {ENTRY_FILE}"
        else:
            try:
                module = _import(item / ENTRY_FILE, item.name)
                plugin.tools = sorted(getattr(module, "HANDLERS", {}) or {})
                meta_name = getattr(module, "META", {}).get("name")
                if meta_name and not plugin.description:
                    plugin.description = getattr(module, "META", {}).get("description", "")
            except PluginError as exc:
                plugin.error = str(exc)
        result.append(plugin)
    return result


def load_tools() -> tuple[list[dict], dict]:
    """加载所有启用的插件，返回 (schemas, handlers)。坏的跳过，不拖垮启动。"""
    schemas: list[dict] = []
    handlers: dict = {}
    for plugin in list_plugins():
        if not plugin.enabled or plugin.error:
            continue
        try:
            module = _import(plugin.path / ENTRY_FILE, plugin.name)
            for schema in getattr(module, "SCHEMAS", []) or []:
                name = schema.get("function", {}).get("name")
                if name and name not in handlers:
                    schemas.append(schema)
            for name, fn in (getattr(module, "HANDLERS", {}) or {}).items():
                if callable(fn) and name not in handlers:
                    handlers[name] = fn
        except PluginError:
            continue
    return schemas, handlers


def _import(file: Path, alias: str):
    """把插件目录里的 plugin.py 当独立模块导入。"""
    module_name = f"miniagent_plugin_{alias}"
    if module_name in sys.modules:
        return sys.modules[module_name]

    spec = importlib.util.spec_from_file_location(module_name, file)
    if spec is None or spec.loader is None:
        raise PluginError(f"无法加载 {file}")

    module = importlib.util.module_from_spec(spec)
    sys.modules[module_name] = module
    try:
        spec.loader.exec_module(module)
    except Exception as exc:  # noqa: BLE001 插件代码任何异常都算加载失败
        sys.modules.pop(module_name, None)
        raise PluginError(f"导入失败：{type(exc).__name__}: {exc}") from exc

    if not isinstance(getattr(module, "HANDLERS", None), dict):
        sys.modules.pop(module_name, None)
        raise PluginError("plugin.py 里没有 HANDLERS 字典")
    return module


# ---------------------------------------------------------------- 安装
# 下载与解压的公共实现见 ../fetch.py（技能安装共用同一套）。


def install(source: str, name: str | None = None, subdir: str | None = None) -> Plugin:
    """从 GitHub 地址或本地目录安装插件。"""
    source = source.strip()
    if not source:
        raise PluginError("地址不能为空")

    local = Path(source).expanduser()
    if local.is_dir():  # 本地目录，方便离线开发和测试
        if not (local / ENTRY_FILE).is_file():
            raise PluginError(f"目录里没有 {ENTRY_FILE}")
        target_name = name or local.name
        dest = PLUGIN_DIR / target_name
        if dest.exists():
            shutil.rmtree(dest)
        PLUGIN_DIR.mkdir(parents=True, exist_ok=True)
        shutil.copytree(local, dest, ignore=shutil.ignore_patterns("__pycache__", "*.pyc"))
        return _finalize(dest, source=source, version=None)

    if "github.com" in source:
        return _install_from_github(source, name=name, subdir=subdir)

    raise PluginError("只支持 GitHub 地址或本地目录")


def _install_from_github(source: str, name: str | None, subdir: str | None) -> Plugin:
    # https://github.com/{owner}/{repo}[/tree/{branch}[/subdir] | /blob/{branch}/{path}]
    parts = source.rstrip("/").split("github.com/", 1)[-1].split("/")
    if len(parts) < 2:
        raise PluginError("GitHub 地址格式不对，应该形如 https://github.com/owner/repo")

    repo = f"{parts[0]}/{parts[1]}"
    branch = None
    if len(parts) >= 4 and parts[2] in ("tree", "blob"):
        branch = parts[3]
        if parts[2] == "tree" and len(parts) >= 5:
            subdir = "/".join(parts[4:])
        if parts[2] == "blob":
            # 单文件：直接把那个 .py 存成 plugin.py
            path = "/".join(parts[4:])
            raw = f"https://raw.githubusercontent.com/{repo}/{branch}/{path}"
            target_name = name or Path(path).stem
            dest = PLUGIN_DIR / target_name
            dest.mkdir(parents=True, exist_ok=True)
            (dest / ENTRY_FILE).write_bytes(fetch.download(raw))
            return _finalize(dest, source=source, version=None)

    payload, ref = fetch.github_zip(repo, branch)

    target_name = name or parts[1]
    dest = PLUGIN_DIR / target_name
    if dest.exists():
        shutil.rmtree(dest)
    prefix = fetch.zip_prefix(payload, subdir, ENTRY_FILE)
    extracted = fetch.extract_zip(payload, prefix, dest)
    if not extracted:
        raise fetch.FetchError(f"压缩包里没有可解出的文件：{source}（分支 {ref}）")

    return _finalize(dest, source=f"{source}（分支 {ref}）", version=None)


def _finalize(dest: Path, source: str, version: str | None) -> Plugin:
    """校验能加载，写 meta.json，清掉旧模块缓存。"""
    try:
        module = _import(dest / ENTRY_FILE, dest.name)
    except PluginError:
        shutil.rmtree(dest, ignore_errors=True)
        raise

    meta_in = getattr(module, "META", {}) or {}
    meta = {
        "name": dest.name,
        "source": source,
        "version": version or str(meta_in.get("version", "—")),
        "description": str(meta_in.get("description", "")),
    }
    (dest / META_FILE).write_text(json.dumps(meta, ensure_ascii=False, indent=2), encoding="utf-8")
    _set_enabled(dest.name, True)

    return Plugin(
        name=dest.name,
        path=dest,
        enabled=True,
        source=meta["source"],
        version=meta["version"],
        description=meta["description"],
        tools=sorted(getattr(module, "HANDLERS", {}) or {}),
    )


def set_enabled(name: str, enabled: bool) -> None:
    state = _state()
    state[name] = bool(enabled)
    _save_state(state)


def remove(name: str) -> None:
    target = PLUGIN_DIR / name
    if not target.is_dir():
        raise PluginError(f"插件不存在：{name}")
    shutil.rmtree(target)
    state = _state()
    state.pop(name, None)
    _save_state(state)
    sys.modules.pop(f"miniagent_plugin_{name}", None)
