"""模型层：协议无关的中间表示。

不管供应商用哪种原生协议，出了适配器就只剩四类事件：
    文本增量、工具调用增量、用量、结束

内部消息格式沿用 OpenAI Chat 的形状（role / content / tool_calls / tool_call_id），
因为其余各层已经按这个形状写好了。适配器负责「出去时翻译成对方格式，进来时翻译回这个形状」。
"""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass
class HttpRequest:
    """适配器产出的、可直接发出去的请求。"""

    url: str
    headers: dict[str, str]
    body: bytes


@dataclass
class RequestContext:
    """一次请求的全部输入，交给适配器翻译。"""

    model_id: str
    messages: list[dict]
    tools: list[dict] | None
    temperature: float
    max_tokens: int
    stream: bool
    api_key: str
    base_url: str
    quirks: dict = field(default_factory=dict)

    @property
    def base(self) -> str:
        """去掉尾斜杠的接口地址。协议层拼 URL 统一从这里拿，别再各自 rstrip。"""
        return self.base_url.rstrip("/")


# ---------------------------------------------------------------- 事件


@dataclass
class TextDelta:
    text: str


@dataclass
class ToolCallDelta:
    """工具调用的增量片段：名字和参数都可能分多次到达。"""

    index: int
    call_id: str | None = None
    name: str | None = None
    arguments: str = ""


@dataclass
class Usage:
    input_tokens: int = 0
    output_tokens: int = 0


@dataclass
class Finished:
    reason: str = ""


Event = TextDelta | ToolCallDelta | Usage | Finished


class StreamParser:
    """按行喂 SSE，吐出统一事件。各协议自己实现 feed。"""

    def feed(self, line: bytes) -> list[Event]:
        return []

    def finish(self) -> list[Event]:
        return []
