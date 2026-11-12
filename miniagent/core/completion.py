"""核心层：完成度判定。

两个层次：
  快判（每步都跑）—— 纯规则打分，看工具是否报错、有没有产出，零成本。
  验证（循环出口）—— 不再问模型「做完了吗」就信（同一个模型给自己盖章等于没盖章），
                      而是让它给出一只能机械证明完成的工具调用（读回文件 / 跑测试 /
                      列目录），内核真的执行它，用结果说话。纯问答任务无法验证，
                      允许 NOVERIFY 放行，但不给「已验证」标记。

验证探针的返回分四类：
  有工具调用   → 交给内核执行，结果用 is_failure 判定
  TODO: xxx    → 模型自己承认没做完，按旧通道打回
  NOVERIFY: x  → 声明无法工具验证（纯问答），放行
  其他 / 出错  → 放行，但不算已验证 —— 宁可少一个标记，不能假造一个证据
"""

from __future__ import annotations

import re

from .state import TaskState

ERROR_MARKERS = (
    "执行异常",
    "参数不匹配",
    "文件不存在",
    "命令超时",
    "请求失败",
    "写入失败",
    "读取失败",
    "exit_code=1",
    "被规则拒绝",
)

VERIFY_PROMPT = (
    "只做一件事：核实你刚才声称的任务是否真的完成了。不要凭记忆下结论。\n"
    "给出一只能证明它的工具调用，例如：读回刚写的文件确认内容、跑一次测试或命令、"
    "列出目标目录确认产物存在。\n"
    "如果这个任务不涉及工具产物（纯问答、纯解释），无法用工具验证，"
    "就只输出一行 NOVERIFY: 原因（20 字以内）。没做完就输出 TODO: 还差什么。"
)

# 探针最多要几个验证调用，防止模型拿一堆工具调用把预算烧穿
MAX_VERIFY_CALLS = 3


class CompletionJudge:
    def __init__(self, llm=None, enabled: bool = True) -> None:
        self.llm = llm
        self.enabled = enabled and llm is not None

    def heuristic(self, state: TaskState, recent_results: list[str]) -> float:
        """规则快判：0.1 ~ 0.95。"""
        if state.tool_calls == 0:
            return 0.4
        errors = sum(1 for text in recent_results if any(mark in text for mark in ERROR_MARKERS))
        score = 0.55 + (0.15 if errors == 0 else -0.12 * errors)
        if state.step >= 2 and errors == 0:
            score += 0.1
        return max(0.1, min(0.95, score))

    def verify(self, messages: list[dict], tools: list[dict] | None = None) -> tuple[str, object]:
        """循环出口验证。返回 (类型, 载荷)：

        ("call", [tool_call, ...])  模型给了一只能证明完成的工具调用
        ("todo", 原因)              模型明确说没做完
        ("noverify", 原因)          声明无法工具验证（纯问答），放行
        ("noverify", "")            探针失败 / 回答含糊，同样放行但不算已验证
        """
        if not self.enabled:
            return "noverify", ""
        probe = list(messages) + [{"role": "user", "content": VERIFY_PROMPT}]
        try:
            text, calls = self.llm.stream(probe, tools)
        except Exception:  # noqa: BLE001 探针失败不该影响交付
            return "noverify", ""

        if calls:
            return "call", list(calls[:MAX_VERIFY_CALLS])

        body = (text or "").strip()
        match = re.search(r"TODO[:：]?\s*(.+)", body, re.S)
        if match:
            return "todo", match.group(1).strip().replace("\n", " ")[:80]
        nv = re.search(r"NOVERIFY[:：]?\s*(.*)", body, re.I)
        if nv:
            return "noverify", nv.group(1).strip()[:60]
        # 模型只说 DONE 没给验证手段：无法机械核实，按未验证放行
        return "noverify", ""
