"""工具层公用小工具。"""

from __future__ import annotations

from ..config import MAX_TOOL_OUTPUT
from ..markers import ERR, OK, ERR_TAG, OK_TAG  # noqa: F401  再导出：工具模块从这里拿标记

# 结构化结果标记的约定见 ../markers.py。
# 前缀字符串必须和 core/guard.py 的 ERR_PREFIX / OK_PREFIX 一致，test_guard 里有断言锁着。

# 成功结果里出现这些词，会被关键词兜底逻辑误判成失败，需要主动豁免
_AMBIGUOUS = ("失败", "不存在", "超时", "超出", "不支持", "无法还原", "解析失败", "被拒绝")


def truncate(text: str, limit: int = MAX_TOOL_OUTPUT) -> str:
    if len(text) <= limit:
        return text
    return text[:limit] + f"\n...（已截断，原长 {len(text)} 字符）"


def tag_result(text: str) -> str:
    """给没有前缀的结果补标记。

    只做一件事：成功结果里含歧义词时加 OK，免得熔断把正常内容当失败。
    真正的失败由工具自己打 ERR，这里不猜。
    """
    if not text:
        return text
    head = text[:40].lstrip()
    if head.startswith(ERR.strip()) or head.startswith(OK.strip()):
        return text
    if any(word in text[:200] for word in _AMBIGUOUS):
        return OK + text
    return text
