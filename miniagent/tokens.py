"""token 估算。

单独放一个叶子模块（只依赖 re），因为算 token 的地方横跨核心层、工具层和
MCP 层：谁都不该为了算个近似值去 import 整个 core（那会把导入链绕成环）。

粗估规则：中文 1 字 ≈ 1 token，其余字符 4 个 ≈ 1 token。
不追求精确——我们要的是「预算够不够」这个量级判断，不是账单。
"""

from __future__ import annotations

import re

_CJK = re.compile(r"[\u4e00-\u9fff\u3000-\u303f\uff00-\uffef]")


def estimate_text(text: str) -> int:
    if not text:
        return 0
    cjk = len(_CJK.findall(text))
    other = len(text) - cjk
    return cjk + max(1, other // 4) if other else cjk


def take_prefix(text: str, max_tokens: int) -> str:
    """从头取，token 不超过 max_tokens 的最长前缀。"""
    if max_tokens <= 0:
        return ""
    if estimate_text(text) <= max_tokens:
        return text
    low, high = 0, len(text)
    while low < high:  # estimate_text 单调，二分找边界
        mid = (low + high + 1) // 2
        if estimate_text(text[:mid]) <= max_tokens:
            low = mid
        else:
            high = mid - 1
    return text[:low]


def take_suffix(text: str, max_tokens: int) -> str:
    """从尾取，token 不超过 max_tokens 的最长后缀。"""
    if max_tokens <= 0:
        return ""
    if estimate_text(text) <= max_tokens:
        return text
    low, high = 0, len(text)
    while low < high:
        mid = (low + high + 1) // 2
        if estimate_text(text[-mid:]) <= max_tokens:
            low = mid
        else:
            high = mid - 1
    return text[-low:] if low else ""


def clip(text: str, max_tokens: int, *, sentence: bool = True, ellipsis: str = "…") -> str:
    """截到 max_tokens 以内，尽量在句子边界收尾。"""
    text = re.sub(r"\s+", " ", str(text or "")).strip()
    if estimate_text(text) <= max_tokens:
        return text

    head = take_prefix(text, max_tokens)
    if sentence:
        for mark in ("。", ". ", "；", ";", "，", ", "):
            cut = head.rfind(mark)
            if cut >= len(head) * 0.5:
                return head[: cut + 1].strip()
    return head.rstrip() + ellipsis
