"""核心层：任务状态观测。

系统要有传感器，这就是那个传感器：每走一步都把「我在第几步、工具调了几次、
上下文占了多少、任务完成到几成、卡在什么状态」记下来，交给界面或日志展示。
"""

from __future__ import annotations

from dataclasses import asdict, dataclass


@dataclass
class TaskState:
    status: str = "idle"  # idle / planning / tooling / checking / done / blocked / error / stopped
    step: int = 0
    tool_calls: int = 0
    context_tokens: int = 0
    context_budget: int = 0
    output_tokens: int = 0
    completion: float = 0.0  # 0..1 估算完成度
    verified: bool = False  # 交付前有没有用工具结果做过机械验证
    note: str = ""

    @property
    def context_ratio(self) -> float:
        if not self.context_budget:
            return 0.0
        return min(1.0, self.context_tokens / self.context_budget)

    def snapshot(self) -> dict:
        data = asdict(self)
        data["context_ratio"] = round(self.context_ratio, 3)
        data["completion"] = round(self.completion, 2)
        return data
