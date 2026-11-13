"""插件层：把 GitHub 上的工具插件装进本地。

对外只暴露四个动作：list_plugins / install / set_enabled / remove，加上给工具层用的 load_tools。
"""

from .manager import (
    PLUGIN_DIR,
    Plugin,
    PluginError,
    install,
    list_plugins,
    load_tools,
    remove,
    set_enabled,
)

__all__ = [
    "PLUGIN_DIR",
    "Plugin",
    "PluginError",
    "install",
    "list_plugins",
    "load_tools",
    "remove",
    "set_enabled",
]
