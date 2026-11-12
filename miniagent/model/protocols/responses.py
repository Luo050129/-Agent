"""OpenAI Responses API 协议（新版接口，不是 chat/completions）。

差异点：
  · 路径是 /responses，请求体用 input 数组而不是 messages
  · 工具定义是扁平的 {type:function, name, ...}，不是嵌在 function 里
  · 工具调用是独立的 function_call item，结果用 function_call_output item
  · 流式事件有自己的名字（response.output_text.delta 等）
"""

from __future__ import annotations

import json

from ..wire import Event, Finished, HttpRequest, RequestContext, StreamParser, TextDelta, ToolCallDelta, Usage
from .base import Protocol, SseParser


def _convert(messages: list[dict]) -> list[dict]:
    """OpenAI Chat 形状 → Responses 的 input 数组。"""
    items: list[dict] = []

    for msg in messages:
        role = msg.get("role")

        if role in ("system", "developer"):
            text = str(msg.get("content") or "").strip()
            if text:
                items.append({"role": "system", "content": [{"type": "input_text", "text": text}]})
            continue

        if role == "tool":
            items.append(
                {
                    "type": "function_call_output",
                    "call_id": msg.get("tool_call_id") or "",
                    "output": str(msg.get("content") or ""),
                }
            )
            continue

        if role == "assistant":
            text = str(msg.get("content") or "")
            if text:
                items.append({"role": "assistant", "content": [{"type": "output_text", "text": text}]})
            for call in msg.get("tool_calls") or []:
                fn = call.get("function") or {}
                items.append(
                    {
                        "type": "function_call",
                        "call_id": call.get("id") or "call_0",
                        "name": fn.get("name") or "",
                        "arguments": fn.get("arguments") or "{}",
                    }
                )
            continue

        items.append({"role": "user", "content": [{"type": "input_text", "text": str(msg.get("content") or "")}]})

    return items


class ResponsesProtocol(Protocol):
    name = "responses"
    label = "OpenAI Responses"

    def build(self, ctx: RequestContext) -> HttpRequest:
        base = ctx.base
        url = base if base.endswith("/responses") else f"{base}/responses"

        payload: dict = {
            "model": ctx.model_id,
            "input": _convert(ctx.messages),
            "stream": ctx.stream,
            "temperature": ctx.temperature,
        }
        if ctx.max_tokens:
            payload["max_output_tokens"] = ctx.max_tokens

        if ctx.tools:
            payload["tools"] = [
                {
                    "type": "function",
                    "name": t.get("function", {}).get("name", ""),
                    "description": t.get("function", {}).get("description", ""),
                    "parameters": t.get("function", {}).get("parameters") or {"type": "object", "properties": {}},
                }
                for t in ctx.tools
                if t.get("function")
            ]

        headers = self.bearer(ctx.api_key)
        headers["Accept"] = "text/event-stream"
        headers.update(ctx.quirks.get("headers", {}))
        return HttpRequest(url=url, headers=headers, body=json.dumps(payload, ensure_ascii=False).encode())

    def parser(self) -> StreamParser:
        return ResponsesParser()


class ResponsesParser(SseParser):
    def __init__(self) -> None:
        super().__init__()
        self._index: dict[str, int] = {}

    def _slot(self, item_id: str) -> int:
        if item_id not in self._index:
            self._index[item_id] = len(self._index)
        return self._index[item_id]

    def on_data(self, payload: dict, event: str) -> list[Event]:
        kind = payload.get("type") or event
        out: list[Event] = []

        if kind == "response.output_text.delta":
            if payload.get("delta"):
                out.append(TextDelta(str(payload["delta"])))
            return out

        if kind == "response.output_item.added":
            item = payload.get("item") or {}
            if item.get("type") == "function_call":
                out.append(
                    ToolCallDelta(
                        index=self._slot(str(item.get("id") or item.get("call_id") or "0")),
                        call_id=item.get("call_id") or item.get("id"),
                        name=item.get("name"),
                    )
                )
            return out

        if kind == "response.function_call_arguments.delta":
            if payload.get("delta"):
                out.append(
                    ToolCallDelta(
                        index=self._slot(str(payload.get("item_id") or "0")),
                        arguments=str(payload["delta"]),
                    )
                )
            return out

        if kind == "response.completed":
            usage = ((payload.get("response") or {}).get("usage")) or {}
            if usage:
                out.append(
                    Usage(
                        input_tokens=int(usage.get("input_tokens") or 0),
                        output_tokens=int(usage.get("output_tokens") or 0),
                    )
                )
            out.append(Finished("completed"))
            return out

        if kind in ("response.failed", "error"):
            raise RuntimeError(str(payload.get("error") or payload)[:300])

        return out
