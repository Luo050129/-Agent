"""MCP 元工具：调用没展开 schema 的工具。

超预算时会有一批工具只留一行签名（省 token），模型照样要能调它们，
于是注册这一个工具当通用入口。参数走 JSON 字符串而不是嵌套对象 ——
四种协议对嵌套 schema 的容忍度不一样，字符串是最大公约数。
"""

from __future__ import annotations

META_SCHEMA = {
    "type": "function",
    "function": {
        "name": "mcp_call",
        "description": (
            "调用未展开定义的 MCP 工具。可用工具在系统提示的「MCP 工具」一节里有签名，"
            "server 和 tool 照那里的名字填。已经展开成独立工具的不需要走这里。"
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "server": {"type": "string", "description": "MCP server 名，例如 browser"},
                "tool": {"type": "string", "description": "工具名，例如 browser_click"},
                "args": {
                    "type": "string",
                    "description": '参数对象，写成 JSON 字符串，例如 {"ref": "a1", "element": "登录按钮"}',
                },
            },
            "required": ["server", "tool"],
        },
    },
}

META_NAME = "mcp_call"
