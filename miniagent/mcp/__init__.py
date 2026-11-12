"""MCP 层：把外部 server 当作第三种工具来源。

    client.py   stdio JSON-RPC 传输（一个 server 一个子进程）
    schema.py   token 压缩：完整 JSON Schema → 能用的最小子集
    manager.py  配置、懒启动、工具注册、结果渲染
    meta.py     超预算工具的通用调用入口

依赖单向：本层只依赖 config / tokens，**不 import tools 也不 import core 的包出口**
（tools 会反过来 import 本层，core 的运行时要拼 MCP 摘要）。
"""

from .client import MCPError, MCPGone, MCPTimeout, StdioClient
from .manager import (
    MCPManager,
    ServerConfig,
    load_config,
    mcp_manager,
    render_result,
    save_config,
    tool_name,
)
from .schema import apply_budget, compact_schema, signature_line, to_openai_tool, tool_tokens

__all__ = [
    "MCPError",
    "MCPGone",
    "MCPTimeout",
    "MCPManager",
    "ServerConfig",
    "StdioClient",
    "apply_budget",
    "compact_schema",
    "load_config",
    "mcp_manager",
    "render_result",
    "save_config",
    "signature_line",
    "to_openai_tool",
    "tool_name",
    "tool_tokens",
]
