"""限额自洽性检查：把「拍出来的数」变成「有依据、可回归的数」。

每个默认值都要落在区间里，并且和其他配置对得上。数值变了但依据没变，这里会先叫。

依据与实测见 README「这些阈值是怎么定的」；重测用 `python tests/bench_limits.py`。
"""

from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from miniagent.config import DEFAULT_CONTEXT_TOKENS, context_budget  # noqa: E402
from miniagent.core.context import ContextManager  # noqa: E402
from miniagent.core.guard import Limits  # noqa: E402

# 单步上下文成本估算：工具结果上限（token）+ 参数与回复
TOOL_RESULT_TOKENS = ContextManager().tool_result_tokens
EST_STEP_TOKENS = TOOL_RESULT_TOKENS + 300
SLOWEST_TOOL_S = 60  # run_shell / run_python 默认超时


def main() -> int:
    print("[limits] 开始")
    limits = Limits()
    checks: list[tuple[str, bool]] = []

    # 1. 同一调用重复次数：2 太少（连一次容错都没有），超过 5 就是放任原地打转
    checks.append((f"max_same_call={limits.max_same_call} 在 [2,5]", 2 <= limits.max_same_call <= 5))

    # 2. 同一调用失败次数：1 次容错 + 1 次确认
    checks.append(
        (f"max_call_failures={limits.max_failures_per_call} 在 [1,3]", 1 <= limits.max_failures_per_call <= 3)
    )

    # 3. 无进展判定：至少 3 次才能说「连续相同」
    checks.append((f"max_unproductive={limits.max_unproductive} 在 [3,6]", 3 <= limits.max_unproductive <= 6))

    # 4. 连续失败：大约两轮全失败（每轮 2-3 个工具）
    checks.append((f"max_fail_streak={limits.max_fail_streak} 在 [4,10]", 4 <= limits.max_fail_streak <= 10))

    # 5. 工具总数要和上下文预算对得上：40 × 1.8k ≈ 72k，不超过预算的 2.5 倍
    step_budget = limits.max_tool_calls * EST_STEP_TOKENS
    checks.append(
        (f"max_tool_calls={limits.max_tool_calls} 与上下文预算匹配（{step_budget} ≤ {DEFAULT_CONTEXT_TOKENS * 2.5:.0f}）",
         step_budget <= DEFAULT_CONTEXT_TOKENS * 2.5)
    )

    # 6. 时间预算要容得下「最慢工具 × 至少 3 步」，但不能长到让人觉得挂了
    checks.append(
        (f"max_seconds={limits.max_seconds:.0f} ≥ 3 次最慢工具（{SLOWEST_TOOL_S * 3}s）",
         limits.max_seconds >= SLOWEST_TOOL_S * 3)
    )
    checks.append((f"max_seconds={limits.max_seconds:.0f} ≤ 900", limits.max_seconds <= 900))

    # 7. 步数和工具数的关系：一步可以调多个工具，工具上限必须 ≥ 步数
    checks.append(
        (f"max_tool_calls ≥ max_steps（{limits.max_tool_calls} ≥ {limits.max_steps}）",
         limits.max_tool_calls >= limits.max_steps)
    )

    # 8. 环境变量能覆盖（运维时需要）
    os.environ["AGENT_MAX_TOOL_CALLS"] = "7"
    try:
        checks.append(("环境变量能覆盖工具上限", Limits.from_env().max_tool_calls == 7))
    finally:
        os.environ.pop("AGENT_MAX_TOOL_CALLS", None)

    # 9. 预算也不能配得离谱
    os.environ["AGENT_CONTEXT_TOKENS"] = "100"
    try:
        checks.append(("上下文预算有下限保护", context_budget() >= 4000))
    finally:
        os.environ.pop("AGENT_CONTEXT_TOKENS", None)

    for label, ok in checks:
        print(f"  {'PASS' if ok else 'FAIL'}  {label}")
    ok_all = all(ok for _, ok in checks)
    print(f"[limits] {'全部通过' if ok_all else '存在失败项'}")
    return 0 if ok_all else 1


if __name__ == "__main__":
    raise SystemExit(main())
