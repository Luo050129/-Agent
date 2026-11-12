"""模型层：传输客户端。

只做四件事：拼请求上下文、发 HTTP、按协议解析流、把事件拼成 (文本, 工具调用)。
协议差异全在 protocols/ 里，这里不认识任何供应商。
"""

from __future__ import annotations

import os
import time
import urllib.error
import urllib.request

from ..config import KEY_ENV_NAMES, ConfigError, api_key, default_base_url, default_model
from .protocols import get as get_protocol
from .registry import ModelSpec, find
from .wire import RequestContext, TextDelta, ToolCallDelta, Usage

LLMError = ConfigError


class _Transient(Exception):
    """可重试的失败：网络抖动、5xx、响应停滞。"""


def _env_float(name: str, default: float) -> float:
    try:
        return max(1.0, float(os.getenv(name, "")))
    except ValueError:
        return default


def _env_int(name: str, default: int) -> int:
    try:
        return max(0, int(os.getenv(name, "")))
    except ValueError:
        return default


def _hinted_key(names: tuple[str, ...]) -> str | None:
    for name in names:
        value = os.getenv(name)
        if value:
            return value.strip()
    return None


class LLM:
    """一套接口，背后是任意协议、任意供应商。

        llm = LLM("Claude Sonnet 4.5")     # Anthropic 原生
        llm = LLM("Gemini 2.5 Flash")      # Gemini 原生
        llm = LLM("DeepSeek V4 Flash")     # OpenAI 兼容
        text, calls = llm.stream(messages, tools, on_text=print)
    """

    def __init__(
        self,
        model: str | None = None,
        base_url: str | None = None,
        api_key_value: str | None = None,
        temperature: float = 0.3,
        timeout: float | None = None,
        call_deadline: float | None = None,
        retries: int | None = None,
        max_tokens: int = 4096,
    ) -> None:
        spec: ModelSpec | None = find(model) or (find(os.getenv("AGENT_MODEL")) if not model else None)
        self.spec = spec

        if spec is not None:
            self.label = spec.key
            self.model = spec.model
            self.protocol_name = spec.protocol
            base = base_url or spec.base_url
            candidates = spec.key_env + KEY_ENV_NAMES
            quirks = dict(spec.quirks)
        else:
            self.label = model or default_model()
            self.model = self.label
            self.protocol_name = "openai"
            base = base_url or default_base_url()
            candidates = KEY_ENV_NAMES
            quirks = {}

        self.protocol = get_protocol(self.protocol_name)
        self.base_url = base.rstrip("/")
        self.quirks = quirks
        self._key = api_key_value or _hinted_key(candidates) or _optional_key()
        self.temperature = temperature
        self.max_tokens = max_tokens
        self.timeout = timeout if timeout is not None else _env_float("AGENT_LLM_TIMEOUT", 60)
        self.call_deadline = (
            call_deadline if call_deadline is not None else _env_float("AGENT_LLM_DEADLINE", 180)
        )
        self.retries = retries if retries is not None else _env_int("AGENT_LLM_RETRIES", 2)
        self.last_attempts = 0
        self.last_usage: Usage | None = None

    # ---------------------------------------------------------- 对外

    @property
    def url(self) -> str:
        """便于排错：当前协议会把请求发到哪。"""
        return self.protocol.build(self._request_context([], None)).url

    def stream(self, messages: list[dict], tools: list[dict] | None = None, on_text=None):
        """统一入口：返回 (完整文本, tool_calls)。"""
        emitted = {"any": False}

        def _emit(piece: str) -> None:
            emitted["any"] = True
            if on_text:
                on_text(piece)

        last_error: Exception | None = None
        for attempt in range(self.retries + 1):
            self.last_attempts = attempt + 1
            try:
                return self._stream_once(messages, tools, _emit)
            except _Transient as exc:
                last_error = exc
                if emitted["any"] or attempt >= self.retries:
                    break
                time.sleep(min(2**attempt, 4))

        raise ConfigError(f"模型调用失败（已试 {self.last_attempts} 次）：{last_error}")

    def complete(self, messages: list[dict], tools: list[dict] | None = None):
        """不关心流式的场合用这个。"""
        return self.stream(messages, tools, on_text=None)

    # ---------------------------------------------------------- 内部

    def _request_context(self, messages: list[dict], tools: list[dict] | None) -> RequestContext:
        return RequestContext(
            model_id=self.model,
            messages=messages,
            tools=tools,
            temperature=self.temperature,
            max_tokens=self.max_tokens,
            stream=True,
            api_key=self._key,
            base_url=self.base_url,
            quirks=self.quirks,
        )

    def _stream_once(self, messages: list[dict], tools: list[dict] | None, on_text):
        request = self.protocol.build(self._request_context(messages, tools))
        req = urllib.request.Request(request.url, data=request.body, headers=request.headers, method="POST")

        try:
            resp = urllib.request.urlopen(req, timeout=self.timeout)
        except urllib.error.HTTPError as exc:
            detail = exc.read().decode("utf-8", "replace")[:800]
            if exc.code >= 500 or exc.code == 429:
                raise _Transient(f"接口返回 {exc.code}：{detail[:200]}") from exc
            raise ConfigError(f"接口返回 {exc.code}：{detail}") from exc
        except urllib.error.URLError as exc:
            raise _Transient(f"连不上 {request.url}：{exc.reason}") from exc
        except (TimeoutError, OSError) as exc:
            raise _Transient(f"连接超时（{self.timeout:.0f}s）：{exc}") from exc

        text_parts: list[str] = []
        calls: dict[int, dict] = {}
        parser = self.protocol.parser()
        deadline = time.monotonic() + self.call_deadline

        def handle(events) -> None:
            for event in events:
                if isinstance(event, TextDelta):
                    text_parts.append(event.text)
                    on_text(event.text)
                elif isinstance(event, ToolCallDelta):
                    slot = calls.setdefault(
                        event.index,
                        {"id": "", "type": "function", "function": {"name": "", "arguments": ""}},
                    )
                    if event.call_id and not slot["id"]:
                        slot["id"] = event.call_id
                    if event.name and event.name not in slot["function"]["name"]:
                        slot["function"]["name"] += event.name
                    if event.arguments:
                        slot["function"]["arguments"] += event.arguments
                elif isinstance(event, Usage):
                    self.last_usage = event

        try:
            with resp:
                for raw in resp:
                    if time.monotonic() > deadline:
                        raise _Transient(f"响应超过总时限 {self.call_deadline:.0f}s")
                    handle(parser.feed(raw))
                handle(parser.finish())
        except TimeoutError as exc:
            raise _Transient(f"读取响应超时（{self.timeout:.0f}s 无数据）：{exc}") from exc
        except OSError as exc:
            raise _Transient(f"连接中断：{exc}") from exc
        except RuntimeError as exc:  # 协议层解析出来的服务端错误
            raise ConfigError(str(exc)) from exc

        for index, slot in calls.items():
            if not slot["id"]:
                slot["id"] = f"call_{index}"
            if not slot["function"]["arguments"]:
                slot["function"]["arguments"] = "{}"

        return "".join(text_parts), [calls[i] for i in sorted(calls)]


def _optional_key() -> str:
    """本地模型（Ollama / vLLM / LM Studio）不需要 Key，给个占位。"""
    try:
        return api_key()
    except ConfigError:
        return "not-needed"
