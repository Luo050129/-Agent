"""模型层：协议适配基类。

一个协议要干两件事：
    build(ctx)  把统一请求翻译成对方的 HTTP 请求
    parser()    造一个流解析器，把对方的 SSE 翻译回统一事件

新增协议 = 继承 + 实现这两个方法，再在 __init__ 里登记，客户端不用改。
"""

from __future__ import annotations

import json

from ..wire import Event, Finished, HttpRequest, RequestContext, StreamParser, TextDelta, ToolCallDelta, Usage


class Protocol:
    name: str = ""
    label: str = ""
    # 能力标记，界面和调用方可以据此禁用功能
    supports_tools: bool = True
    supports_stream: bool = True

    def build(self, ctx: RequestContext) -> HttpRequest:
        raise NotImplementedError

    def parser(self) -> StreamParser:
        raise NotImplementedError

    # 便于子类复用的鉴权头
    @staticmethod
    def bearer(api_key: str) -> dict[str, str]:
        return {"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"}


class SseParser(StreamParser):
    """SSE 通用外壳：按行切出 event / data，交给子类 on_data。"""

    def __init__(self) -> None:
        self._event = ""

    def feed(self, line: bytes) -> list[Event]:
        text = line.decode("utf-8", "replace").strip()
        if not text or text.startswith(":"):
            return []
        if text.startswith("event:"):
            self._event = text[6:].strip()
            return []
        if not text.startswith("data:"):
            return []

        data = text[5:].strip()
        if not data or data == "[DONE]":
            return []
        try:
            payload = json.loads(data)
        except json.JSONDecodeError:
            return []
        return self.on_data(payload, self._event)

    def on_data(self, payload: dict, event: str) -> list[Event]:
        return []


def tool_schema_to_openai(tool: dict) -> dict:
    """内部工具定义就是 OpenAI 形状，这里做个安全取值。"""
    return tool.get("function", {})


__all__ = [
    "Protocol",
    "SseParser",
    "Event",
    "Finished",
    "TextDelta",
    "ToolCallDelta",
    "Usage",
    "HttpRequest",
    "RequestContext",
]
