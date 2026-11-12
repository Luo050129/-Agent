"""模型层对外出口。

一套接口，四种原生协议，35 个供应商：

    from miniagent.model import LLM
    llm = LLM("Claude Sonnet 4.5")            # 自动走 Anthropic 协议
    text, calls = llm.stream(messages, tools, on_text=print)
"""

from .client import LLM, LLMError
from .protocols import available as protocols_available
from .protocols import describe as protocols_describe
from .registry import (
    CATALOG,
    ModelSpec,
    all_specs,
    by_protocol,
    by_provider,
    default_spec,
    find,
    mainstream_specs,
    protocols,
    providers,
)
from .wire import Event, Finished, TextDelta, ToolCallDelta, Usage

__all__ = [
    "LLM",
    "LLMError",
    "CATALOG",
    "ModelSpec",
    "all_specs",
    "find",
    "default_spec",
    "providers",
    "protocols",
    "by_protocol",
    "by_provider",
    "mainstream_specs",
    "protocols_available",
    "protocols_describe",
    "Event",
    "TextDelta",
    "ToolCallDelta",
    "Usage",
    "Finished",
]
