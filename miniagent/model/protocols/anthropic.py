"""Anthropic Messages 协议。

和 OpenAI 的差异点（也是适配器要处理的全部）：
  · 鉴权是 x-api-key + anthropic-version，不是 Bearer
  · system 是顶层字段，不在 messages 里
  · 工具结果不是 role=tool，而是 user 消息里的 tool_result 块
  · 工具调用不是 tool_calls，而是 assistant 内容里的 tool_use 块
  · 参数是流式的 input_json_delta 片段，要拼
  · max_tokens 必填
"""

from __future__ import annotations

import json

from ..wire import Event, Finished, HttpRequest, RequestContext, StreamParser, TextDelta, ToolCallDelta, Usage
from .base import Protocol, SseParser


def _convert(messages: list[dict]) -> tuple[str, list[dict]]:
    """OpenAI 形状 → Anthropic 形状。"""
    system_parts: list[str] = []
    out: list[dict] = []

    def push(role: str, blocks: list[dict]) -> None:
        # Anthropic 要求 user / assistant 交替；同角色连续就合并内容块
        if out and out[-1]["role"] == role:
            out[-1]["content"].extend(blocks)
        else:
            out.append({"role": role, "content": blocks})

    for msg in messages:
        role = msg.get("role")

        if role == "system":
            text = str(msg.get("content") or "").strip()
            if text:
                system_parts.append(text)
            continue

        if role == "tool":
            push(
                "user",
                [
                    {
                        "type": "tool_result",
                        "tool_use_id": msg.get("tool_call_id") or "",
                        "content": str(msg.get("content") or ""),
                    }
                ],
            )
            continue

        if role == "assistant":
            blocks: list[dict] = []
            text = str(msg.get("content") or "")
            if text:
                blocks.append({"type": "text", "text": text})
            for call in msg.get("tool_calls") or []:
                fn = call.get("function") or {}
                raw = fn.get("arguments") or "{}"
                try:
                    args = json.loads(raw, strict=False) if raw.strip() else {}
                except json.JSONDecodeError:
                    args = {}
                blocks.append(
                    {
                        "type": "tool_use",
                        "id": call.get("id") or "call_0",
                        "name": fn.get("name") or "",
                        "input": args if isinstance(args, dict) else {},
                    }
                )
            if blocks:
                push("assistant", blocks)
            continue

        text = str(msg.get("content") or "")
        push("user", [{"type": "text", "text": text}])

    return "\n\n".join(system_parts), out


class AnthropicProtocol(Protocol):
    name = "anthropic"
    label = "Anthropic Messages"

    def build(self, ctx: RequestContext) -> HttpRequest:
        base = ctx.base
        url = base if base.endswith("/messages") else f"{base}/messages"

        system, converted = _convert(ctx.messages)
        payload: dict = {
            "model": ctx.model_id,
            "messages": converted,
            "max_tokens": ctx.max_tokens or 4096,
            "stream": ctx.stream,
            "temperature": ctx.temperature,
        }
        if system:
            payload["system"] = system

        if ctx.tools:
            payload["tools"] = [
                {
                    "name": t.get("function", {}).get("name", ""),
                    "description": t.get("function", {}).get("description", ""),
                    "input_schema": t.get("function", {}).get("parameters") or {"type": "object", "properties": {}},
                }
                for t in ctx.tools
                if t.get("function")
            ]

        headers = {
            "Content-Type": "application/json",
            "Accept": "text/event-stream",
            "x-api-key": ctx.api_key,
            "anthropic-version": ctx.quirks.get("anthropic_version", "2023-06-01"),
        }
        headers.update(ctx.quirks.get("headers", {}))
        return HttpRequest(url=url, headers=headers, body=json.dumps(payload, ensure_ascii=False).encode())

    def parser(self) -> StreamParser:
        return AnthropicParser()


class AnthropicParser(SseParser):
    def on_data(self, payload: dict, event: str) -> list[Event]:
        kind = payload.get("type") or event
        out: list[Event] = []

        if kind == "message_start":
            usage = (payload.get("message") or {}).get("usage") or {}
            out.append(Usage(input_tokens=int(usage.get("input_tokens") or 0), output_tokens=0))
            return out

        if kind == "content_block_start":
            block = payload.get("content_block") or {}
            if block.get("type") == "tool_use":
                out.append(
                    ToolCallDelta(
                        index=int(payload.get("index", 0)),
                        call_id=block.get("id"),
                        name=block.get("name"),
                    )
                )
            return out

        if kind == "content_block_delta":
            delta = payload.get("delta") or {}
            if delta.get("type") == "text_delta" and delta.get("text"):
                out.append(TextDelta(str(delta["text"])))
            elif delta.get("type") == "input_json_delta" and delta.get("partial_json"):
                out.append(ToolCallDelta(index=int(payload.get("index", 0)), arguments=str(delta["partial_json"])))
            return out

        if kind == "message_delta":
            usage = payload.get("usage") or {}
            if usage.get("output_tokens"):
                out.append(Usage(input_tokens=0, output_tokens=int(usage["output_tokens"])))
            reason = (payload.get("delta") or {}).get("stop_reason")
            if reason:
                out.append(Finished(str(reason)))
            return out

        if kind == "message_stop":
            out.append(Finished("end"))
            return out

        if kind == "error":
            raise RuntimeError(str(payload.get("error"))[:300])

        return out
