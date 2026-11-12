"""包入口。

四层结构：
    model/  模型层 —— 和 LLM 打交道，只管发消息、收流
    core/   核心层 —— ReAct 循环、会话状态、提示词、事件契约
    tools/  工具层 —— Agent 能做的动作，schema + 实现
    ui/     交互层 —— 图形界面 / 命令行，只负责显示和收集输入

依赖方向是单向的：ui → core → (model, tools)，model 和 tools 互不认识。
"""

__version__ = "0.2.0"
