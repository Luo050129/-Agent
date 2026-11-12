"""插件层测试：本地目录安装、GitHub 压缩包安装、启停、卸载、工具注册。

不需要网络（GitHub 下载用假数据替换），`python tests/test_plugins.py` 直接跑。
"""

from __future__ import annotations

import io
import os
import shutil
import sys
import tempfile
import zipfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from miniagent import fetch, plugins  # noqa: E402
from miniagent.plugins import manager  # noqa: E402
from miniagent.tools import TOOL_SCHEMAS, execute, refresh  # noqa: E402

PLUGIN_CODE = '''
META = {"name": "demo", "version": "1.2.3", "description": "演示插件"}


def hello(name: str = "world") -> str:
    return f"hello {name}"


SCHEMAS = [{
    "type": "function",
    "function": {
        "name": "demo_hello",
        "description": "打个招呼",
        "parameters": {"type": "object", "properties": {"name": {"type": "string"}}},
    },
}]

HANDLERS = {"demo_hello": hello}
'''

BAD_CODE = "SCHEMAS = []\n"  # 没有 HANDLERS，应被拒绝


def _names() -> list[str]:
    return [p.name for p in plugins.list_plugins()]


def _tool_names() -> set[str]:
    return {s["function"]["name"] for s in TOOL_SCHEMAS}


def _cleanup(*names: str) -> None:
    for name in names:
        if name in _names():
            plugins.remove(name)


def test_local_install() -> list[tuple[str, bool]]:
    checks = []
    src = tempfile.mkdtemp(prefix="test_local_plugin_")
    try:
        with open(os.path.join(src, "plugin.py"), "w", encoding="utf-8") as fh:
            fh.write(PLUGIN_CODE)
        name = os.path.basename(src)

        plugin = plugins.install(src)
        checks.append(("本地目录安装成功", plugin.name == name))
        checks.append(("读到插件元信息", plugin.version == "1.2.3"))
        checks.append(("插件出现在列表里", name in _names()))

        tools = refresh()
        checks.append(("工具注册表里有插件工具", "demo_hello" in _tool_names()))
        checks.append(("refresh 返回插件工具名", "demo_hello" in tools))
        checks.append(("插件工具能执行", execute("demo_hello", '{"name": "小罗"}') == "hello 小罗"))

        plugins.set_enabled(name, False)
        refresh()
        checks.append(("停用后工具消失", "demo_hello" not in _tool_names()))

        plugins.set_enabled(name, True)
        refresh()
        checks.append(("重新启用后工具回来", "demo_hello" in _tool_names()))

        plugins.remove(name)
        refresh()
        checks.append(("卸载后目录没了", name not in _names()))
        checks.append(("卸载后工具没了", "demo_hello" not in _tool_names()))
    finally:
        shutil.rmtree(src, ignore_errors=True)
    return checks


def test_zip_install() -> list[tuple[str, bool]]:
    """模拟 GitHub 仓库压缩包：外层一个目录，里面才是 plugin.py。"""
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        zf.writestr("repo-main/plugin.py", PLUGIN_CODE)
        zf.writestr("repo-main/README.md", "# demo")
    payload = buf.getvalue()

    original = fetch.download
    fetch.download = lambda url, timeout=30: payload  # noqa: ARG005
    checks = []
    try:
        plugin = plugins.install("https://github.com/fake/repo")
        checks.append(("从仓库压缩包安装成功", plugin.name == "repo"))
        checks.append(("能自动找到 plugin.py 所在目录", "demo_hello" in plugin.tools))
        refresh()
        checks.append(("安装后工具可用", "demo_hello" in _tool_names()))
        checks.append(("meta 里记了来源", "github.com/fake/repo" in plugin.source))
    finally:
        fetch.download = original
        _cleanup("repo")
        refresh()
    return checks


def test_bad_plugin() -> list[tuple[str, bool]]:
    checks = []
    src = tempfile.mkdtemp(prefix="test_bad_plugin_")
    try:
        with open(os.path.join(src, "plugin.py"), "w", encoding="utf-8") as fh:
            fh.write(BAD_CODE)
        name = os.path.basename(src)
        try:
            plugins.install(src)
            checks.append(("缺 HANDLERS 的插件被拒绝", False))
        except plugins.PluginError:
            checks.append(("缺 HANDLERS 的插件被拒绝", True))
        checks.append(("失败后不留垃圾目录", name not in _names()))
    finally:
        shutil.rmtree(src, ignore_errors=True)
        _cleanup(os.path.basename(src))
    return checks


def main() -> int:
    print("[plugins] 开始")
    checks = test_local_install() + test_zip_install() + test_bad_plugin()
    for label, ok in checks:
        print(f"  {'PASS' if ok else 'FAIL'}  {label}")
    ok_all = all(ok for _, ok in checks)
    print(f"[plugins] {'全部通过' if ok_all else '存在失败项'}")
    return 0 if ok_all else 1


if __name__ == "__main__":
    raise SystemExit(main())
