"""全包统一的工具结果标记。

约定（三层判定的第 1 层，由 core/guard.classify 采信）：
    工具主动失败   → 返回值前面加 ERR
    成功但含歧义词 → tag_result 补 OK 豁免
    干净的成功     → 不加前缀，输出好看

这里必须是零依赖的叶子模块：tools、mcp、core 三层都要用，而 mcp 层
禁止 import core/tools（会成环）。以前是 7 个文件各抄一份常量、靠测试
断言锁一致；现在单一来源，抄写和漂移的可能性一起消失。
"""

from __future__ import annotations

# 带尾随空格的消息前缀：工具拼结果时用
ERR = "[ERR] "
OK = "[OK] "

# 裸标记：guard 做 startswith 判定用，不带空格更宽容
ERR_TAG = "[ERR]"
OK_TAG = "[OK]"
