"""工具层出口：内置工具 + 插件工具 + MCP 工具汇总成一张注册表。

三条来源，同一个出口：

    内置  —— 模块里写 SCHEMAS/HANDLERS，这里 import 一次
    插件  —— 界面里装，plugins.load_tools() 并进来
    MCP   —— 配置外部 server，mcp_manager.tools() 并进来（名字带 mcp__ 前缀）

加来源不用改循环：注册表原地 refresh()，核心层只认 name/args/str。
"""

from __future__ import annotations

import json

from ..mcp.manager import mcp_manager
from ..plugins import load_tools as load_plugin_tools
from . import documents, filesystem, runtime, skills_tool, web
from ._util import ERR, tag_result

BUILTIN_SCHEMAS: list[dict] = [
    *runtime.SCHEMAS,
    *filesystem.SCHEMAS,
    *documents.SCHEMAS,
    *skills_tool.SCHEMAS,
    *web.SCHEMAS,
]
BUILTIN_HANDLERS: dict = {
    **runtime.HANDLERS,
    **filesystem.HANDLERS,
    **documents.HANDLERS,
    **skills_tool.HANDLERS,
    **web.HANDLERS,
}
BUILTIN_DANGEROUS: set[str] = (
    runtime.DANGEROUS | filesystem.DANGEROUS | documents.DANGEROUS | skills_tool.DANGEROUS | web.DANGEROUS
)

TOOL_SCHEMAS: list[dict] = []
DISPATCH: dict = {}
DANGEROUS: set[str] = set()


def refresh() -> list[str]:
    """重建注册表（装了插件 / 同步了 MCP 之后调用）。返回插件贡献的工具名。"""
    plugin_schemas, plugin_handlers = load_plugin_tools()
    mcp_schemas, mcp_handlers = mcp_manager.tools()

    TOOL_SCHEMAS.clear()
    TOOL_SCHEMAS.extend([*BUILTIN_SCHEMAS, *plugin_schemas, *mcp_schemas])

    DISPATCH.clear()
    DISPATCH.update(BUILTIN_HANDLERS)
    DISPATCH.update(plugin_handlers)
    DISPATCH.update(mcp_handlers)

    # 插件和 MCP 都是第三方能力，默认按危险操作对待：安全模式下要先确认
    DANGEROUS.clear()
    DANGEROUS.update(BUILTIN_DANGEROUS)
    DANGEROUS.update(plugin_handlers.keys())
    DANGEROUS.update(mcp_handlers.keys())

    return sorted(plugin_handlers)


def mcp_tools() -> tuple[list[dict], dict]:
    """MCP 单独取一次，测试和界面用；refresh() 已经合进主表了。"""
    return mcp_manager.tools()


def execute(name: str, raw_args: str) -> str:
    """按函数名 + JSON 参数执行工具。永不抛异常，错误当结果回灌。

    返回一律带结构化标记：失败以 [ERR] 开头（工具自己打的，或这里兜的），
    成功但内容含歧义词的补 [OK] 豁免，干净的成功结果不加前缀。
    """
    fn = DISPATCH.get(name)
    if fn is None:
        return f"{ERR}未知工具：{name}。可用工具：{', '.join(DISPATCH)}"

    try:
        # strict=False：容忍模型在字符串里直接塞未转义的换行（写多行文件的常见情况）
        args = json.loads(raw_args, strict=False) if raw_args.strip() else {}
        if not isinstance(args, dict):
            return f"{ERR}参数必须是 JSON 对象，收到：{raw_args[:200]}"
    except json.JSONDecodeError as exc:
        return f"{ERR}参数 JSON 解析失败（{exc}）：{raw_args[:200]}"

    try:
        return tag_result(fn(**args))
    except TypeError as exc:
        return f"{ERR}参数不匹配：{exc}"
    except Exception as exc:  # noqa: BLE001 工具内部任何异常都转成文本
        return f"{ERR}工具执行异常：{type(exc).__name__}: {exc}"


refresh()

__all__ = ["TOOL_SCHEMAS", "DISPATCH", "DANGEROUS", "execute", "refresh"]
