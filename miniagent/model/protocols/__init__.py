"""模型层：协议注册表。

加一种新协议：写一个 Protocol 子类，在这里登记一行。客户端和界面都不用改。
"""

from __future__ import annotations

from ...config import ConfigError
from .anthropic import AnthropicProtocol
from .base import Protocol
from .gemini import GeminiProtocol
from .openai_chat import OpenAIChatProtocol
from .responses import ResponsesProtocol

PROTOCOLS: dict[str, Protocol] = {
    p.name: p
    for p in (
        OpenAIChatProtocol(),
        AnthropicProtocol(),
        GeminiProtocol(),
        ResponsesProtocol(),
    )
}


def get(name: str) -> Protocol:
    try:
        return PROTOCOLS[name]
    except KeyError as exc:
        raise ConfigError(f"未知协议：{name}，可用：{', '.join(sorted(PROTOCOLS))}") from exc


def available() -> list[str]:
    return sorted(PROTOCOLS)


def describe() -> dict[str, str]:
    return {name: proto.label for name, proto in PROTOCOLS.items()}


__all__ = ["PROTOCOLS", "Protocol", "get", "available", "describe"]
