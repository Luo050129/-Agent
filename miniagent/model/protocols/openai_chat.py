"""OpenAI Chat Completions 协议（兼容面最广的一种）。

绝大多数供应商——DeepSeek、Moonshot、智谱、通义、SiliconFlow、Groq、Together、
OpenRouter、xAI、Mistral、本地 Ollama / vLLM / LM Studio——都兼容这套形状。
所以 30+ 供应商里，真正需要单独写代码的只有少数几个原生协议。
"""

from __future__ import annotations

import json

from ..wire import Event, Finished, HttpRequest, RequestContext, StreamParser, TextDelta, ToolCallDelta, Usage
from .base import Protocol, SseParser


class OpenAIChatProtocol(Protocol):
    name = "openai"
    label = "OpenAI Chat Completions"

    def build(self, ctx: RequestContext) -> HttpRequest:
        base = ctx.base
        url = base if base.endswith("/chat/completions") else f"{base}/chat/completions"

        payload: dict = {
            "model": ctx.model_id,
            "messages": ctx.messages,
            "stream": ctx.stream,
            "temperature": ctx.temperature,
        }

        # o 系列等模型要 max_completion_tokens，用 quirk 切换字段名
        token_field = ctx.quirks.get("max_tokens_field", "max_tokens")
        if ctx.max_tokens:
            payload[token_field] = ctx.max_tokens

        if ctx.tools:
            payload["tools"] = ctx.tools
            payload["tool_choice"] = "auto"

        if ctx.quirks.get("stream_usage", True):
            payload["stream_options"] = {"include_usage": True}

        headers = self.bearer(ctx.api_key)
        headers["Accept"] = "text/event-stream"
        # Azure 这类供应商不用 Bearer，改走 api-key 头
        if ctx.quirks.get("auth") == "api-key":
            headers.pop("Authorization", None)
            headers["api-key"] = ctx.api_key
        headers.update(ctx.quirks.get("headers", {}))
        return HttpRequest(url=url, headers=headers, body=json.dumps(payload, ensure_ascii=False).encode())

    def parser(self) -> StreamParser:
        return OpenAIChatParser()


class OpenAIChatParser(SseParser):
    def on_data(self, payload: dict, event: str) -> list[Event]:
        out: list[Event] = []

        usage = payload.get("usage")
        if isinstance(usage, dict):
            out.append(
                Usage(
                    input_tokens=int(usage.get("prompt_tokens") or 0),
                    output_tokens=int(usage.get("completion_tokens") or 0),
                )
            )

        if payload.get("error"):
            raise RuntimeError(str(payload["error"])[:300])

        choices = payload.get("choices") or []
        if not choices:
            return out

        delta = choices[0].get("delta") or {}

        content = delta.get("content")
        if content:
            out.append(TextDelta(content))

        for item in delta.get("tool_calls") or []:
            fn = item.get("function") or {}
            out.append(
                ToolCallDelta(
                    index=int(item.get("index", 0)),
                    call_id=item.get("id"),
                    name=fn.get("name"),
                    arguments=fn.get("arguments") or "",
                )
            )

        if choices[0].get("finish_reason"):
            out.append(Finished(str(choices[0]["finish_reason"])))

        return out
