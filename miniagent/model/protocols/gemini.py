"""Google Gemini 协议（generateContent / streamGenerateContent）。

差异点：
  · 路径带模型名和动作：/models/{model}:streamGenerateContent?alt=sse
  · 鉴权用 x-goog-api-key
  · 消息叫 contents，角色是 user / model，内容是 parts
  · 工具调用是 functionCall part，工具结果是 functionResponse part
  · 一次返回的 functionCall 是完整的，不用拼片段
"""

from __future__ import annotations

import json

from ..wire import Event, Finished, HttpRequest, RequestContext, StreamParser, TextDelta, ToolCallDelta, Usage
from .base import Protocol, SseParser


def _convert(messages: list[dict]) -> tuple[list[dict], dict | None]:
    """OpenAI 形状 → Gemini 形状。返回 (contents, systemInstruction)。"""
    system_parts: list[str] = []
    contents: list[dict] = []
    call_names: dict[str, str] = {}  # tool_call_id → 函数名，回填 functionResponse 要用

    def push(role: str, parts: list[dict]) -> None:
        if contents and contents[-1]["role"] == role:
            contents[-1]["parts"].extend(parts)
        else:
            contents.append({"role": role, "parts": parts})

    for msg in messages:
        role = msg.get("role")

        if role == "system":
            text = str(msg.get("content") or "").strip()
            if text:
                system_parts.append(text)
            continue

        if role == "tool":
            name = call_names.get(str(msg.get("tool_call_id")), "")
            push(
                "user",
                [
                    {
                        "functionResponse": {
                            "name": name or "tool",
                            "response": {"result": str(msg.get("content") or "")},
                        }
                    }
                ],
            )
            continue

        if role == "assistant":
            parts: list[dict] = []
            text = str(msg.get("content") or "")
            if text:
                parts.append({"text": text})
            for call in msg.get("tool_calls") or []:
                fn = call.get("function") or {}
                raw = fn.get("arguments") or "{}"
                try:
                    args = json.loads(raw, strict=False) if raw.strip() else {}
                except json.JSONDecodeError:
                    args = {}
                call_names[str(call.get("id"))] = fn.get("name") or ""
                parts.append({"functionCall": {"name": fn.get("name") or "", "args": args}})
            if parts:
                push("model", parts)
            continue

        push("user", [{"text": str(msg.get("content") or "")}])

    system = {"parts": [{"text": "\n\n".join(system_parts)}]} if system_parts else None
    return contents, system


class GeminiProtocol(Protocol):
    name = "gemini"
    label = "Google Gemini"

    def build(self, ctx: RequestContext) -> HttpRequest:
        base = ctx.base
        action = "streamGenerateContent" if ctx.stream else "generateContent"
        suffix = "?alt=sse" if ctx.stream else ""
        url = f"{base}/models/{ctx.model_id}:{action}{suffix}"

        contents, system = _convert(ctx.messages)
        payload: dict = {
            "contents": contents,
            "generationConfig": {
                "temperature": ctx.temperature,
                "maxOutputTokens": ctx.max_tokens,
            },
        }
        if system:
            payload["systemInstruction"] = system

        if ctx.tools:
            payload["tools"] = [
                {
                    "functionDeclarations": [
                        {
                            "name": t.get("function", {}).get("name", ""),
                            "description": t.get("function", {}).get("description", ""),
                            "parameters": _clean_schema(t.get("function", {}).get("parameters") or {}),
                        }
                        for t in ctx.tools
                        if t.get("function")
                    ]
                }
            ]

        headers = {"Content-Type": "application/json", "x-goog-api-key": ctx.api_key}
        headers.update(ctx.quirks.get("headers", {}))
        return HttpRequest(url=url, headers=headers, body=json.dumps(payload, ensure_ascii=False).encode())

    def parser(self) -> StreamParser:
        return GeminiParser()


def _clean_schema(schema: dict) -> dict:
    """Gemini 不认 additionalProperties / $schema 这类 JSON Schema 关键字。"""
    if not isinstance(schema, dict):
        return {}
    cleaned = {}
    for key, value in schema.items():
        if key in ("additionalProperties", "$schema", "strict"):
            continue
        if key == "properties" and isinstance(value, dict):
            cleaned[key] = {k: _clean_schema(v) for k, v in value.items()}
        elif key in ("items",) and isinstance(value, dict):
            cleaned[key] = _clean_schema(value)
        else:
            cleaned[key] = value
    return cleaned


class GeminiParser(SseParser):
    def __init__(self) -> None:
        super().__init__()
        self._call_index = 0

    def on_data(self, payload: dict, event: str) -> list[Event]:
        out: list[Event] = []

        usage = payload.get("usageMetadata")
        if isinstance(usage, dict):
            out.append(
                Usage(
                    input_tokens=int(usage.get("promptTokenCount") or 0),
                    output_tokens=int(usage.get("candidatesTokenCount") or 0),
                )
            )

        if payload.get("error"):
            raise RuntimeError(str(payload["error"])[:300])

        for candidate in payload.get("candidates") or []:
            for part in (candidate.get("content") or {}).get("parts") or []:
                if part.get("text"):
                    out.append(TextDelta(str(part["text"])))
                call = part.get("functionCall")
                if call:
                    out.append(
                        ToolCallDelta(
                            index=self._call_index,
                            call_id=f"call_{self._call_index}",
                            name=call.get("name"),
                            arguments=json.dumps(call.get("args") or {}, ensure_ascii=False),
                        )
                    )
                    self._call_index += 1

            if candidate.get("finishReason"):
                out.append(Finished(str(candidate["finishReason"])))

        return out
