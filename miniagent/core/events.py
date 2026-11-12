"""核心层的事件契约。

核心层不认识任何界面，只往 Sink 里丢事件。
命令行和图形界面各自实现一份，加界面不用碰循环逻辑。
"""

from __future__ import annotations


class Sink:
    """交互层需要实现的事件接口。默认全空实现，方便测试。"""

    def text(self, piece: str) -> None:
        """模型输出的文本增量。"""

    def text_done(self) -> None:
        """本轮文本输出结束。"""

    def tool_call(self, name: str, args: str) -> None:
        """准备调用工具。"""

    def tool_result(self, result: str) -> None:
        """工具返回结果。"""

    def step_done(self, step: int) -> None:
        """完成一步（一轮工具调用）。"""

    def state(self, snapshot: dict) -> None:
        """任务状态快照：完成度、上下文占用、token、当前阶段。"""

    def notice(self, message: str) -> None:
        """非错误的提示：模型重试、熔断、安全提醒等。"""

    def plan(self, steps: list[dict], replanned: bool = False) -> None:
        """开始执行计划（replanned=True 表示这是重规划后的新计划）。"""

    def plan_step(self, index: int, status: str, note: str = "") -> None:
        """某个计划步骤的状态：running / done / failed。"""

    def error(self, message: str) -> None:
        """流程中断或失败。"""

    def confirm(self, name: str, args: str, hint: str = "") -> bool:
        """安全模式下请用户确认危险操作。默认放行。"""
        return True
