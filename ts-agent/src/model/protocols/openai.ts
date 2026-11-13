/**
 * OpenAI Chat Completions 协议。对应 Python 版的 protocols/openai_chat.py。
 *
 * 绝大多数供应商（DeepSeek、Moonshot、Groq、本地 vLLM…）都兼容这一套，
 * 所以它是默认协议。
 */

import {
  SseParser,
  bearer,
  trimBase,
  type HttpRequest,
  type Protocol,
  type RequestContext,
  type StreamEvent,
} from "../../wire.ts";

/** 流式过程中按 index 累积的工具调用 */
interface PendingCall {
  id: string;
  name: string;
  arguments: string;
}

class OpenAiParser extends SseParser {
  private readonly calls = new Map<number, PendingCall>();

  onData(payload: Record<string, unknown>, _event: string): StreamEvent[] {
    const out: StreamEvent[] = [];

    const usage = payload["usage"] as Record<string, number> | undefined;
    if (usage) {
      out.push({
        kind: "usage",
        inputTokens: usage["prompt_tokens"] ?? 0,
        outputTokens: usage["completion_tokens"] ?? 0,
      });
    }

    const choices = payload["choices"] as Array<Record<string, unknown>> | undefined;
    const choice = choices?.[0];
    if (!choice) return out;

    const delta = choice["delta"] as Record<string, unknown> | undefined;
    const text = delta?.["content"];
    if (typeof text === "string" && text) out.push({ kind: "text", text });

    const rawCalls = delta?.["tool_calls"] as Array<Record<string, unknown>> | undefined;
    if (Array.isArray(rawCalls)) {
      for (const raw of rawCalls) {
        const index = typeof raw["index"] === "number" ? raw["index"] : 0;
        const fn = (raw["function"] ?? {}) as Record<string, unknown>;
        const slot = this.calls.get(index) ?? { id: "", name: "", arguments: "" };
        if (typeof raw["id"] === "string" && raw["id"]) slot.id = raw["id"];
        if (typeof fn["name"] === "string" && fn["name"]) slot.name = fn["name"];
        const argsDelta = typeof fn["arguments"] === "string" ? fn["arguments"] : "";
        slot.arguments += argsDelta;
        this.calls.set(index, slot);
        out.push({
          kind: "tool_call",
          index,
          id: raw["id"] as string | undefined,
          name: fn["name"] as string | undefined,
          argumentsDelta: argsDelta || undefined,
        });
      }
    }

    const reason = choice["finish_reason"];
    if (typeof reason === "string" && reason) {
      out.push({ kind: "finished", reason });
    }
    return out;
  }
}

export const openaiProtocol: Protocol = {
  name: "openai",
  supportsTools: true,

  build(ctx: RequestContext): HttpRequest {
    const base = trimBase(ctx.baseUrl);
    const url = base.endsWith("/chat/completions") ? base : `${base}/chat/completions`;

    const payload: Record<string, unknown> = {
      model: ctx.modelId,
      messages: ctx.messages,
      temperature: ctx.temperature,
      max_tokens: ctx.maxTokens,
      stream: ctx.stream,
    };
    if (ctx.stream) payload["stream_options"] = { include_usage: true };
    if (ctx.tools && ctx.tools.length > 0) {
      payload["tools"] = ctx.tools;
      payload["tool_choice"] = "auto";
    }

    return {
      url,
      headers: bearer(ctx.apiKey),
      body: JSON.stringify(payload),
    };
  },

  parser(): SseParser {
    return new OpenAiParser();
  },
};
