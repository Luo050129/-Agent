"""示例插件：演示插件怎么写。

装法（在界面里点「＋ 插件」，地址填这个目录的路径即可）：
    mini_agent/examples/plugin_demo

规则只有三条：
  1. 文件必须叫 plugin.py
  2. 必须暴露 SCHEMAS（OpenAI 工具定义）和 HANDLERS（函数名 → 实现）
  3. 函数参数要和 schema 对得上，返回值只用字符串
"""

from __future__ import annotations

import platform
from datetime import datetime

META = {
    "name": "plugin_demo",
    "version": "1.0.0",
    "description": "示例插件：时间查询与系统信息",
}


def now(fmt: str = "%Y-%m-%d %H:%M:%S") -> str:
    return datetime.now().strftime(fmt)


def sysinfo() -> str:
    return f"{platform.system()} {platform.release()} / Python {platform.python_version()}"


SCHEMAS = [
    {
        "type": "function",
        "function": {
            "name": "demo_now",
            "description": "获取当前日期时间。",
            "parameters": {
                "type": "object",
                "properties": {"fmt": {"type": "string", "description": "strftime 格式，默认 %Y-%m-%d %H:%M:%S"}},
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "demo_sysinfo",
            "description": "获取操作系统和 Python 版本信息。",
            "parameters": {"type": "object", "properties": {}},
        },
    },
]

HANDLERS = {"demo_now": now, "demo_sysinfo": sysinfo}
