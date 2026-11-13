/**
 * 协议无关的中间表示。对应 Python 版的 miniagent/model/wire.py。
 *
 * 不管供应商用哪种原生协议，出了适配器就只剩四类事件：
 *   文本增量、工具调用增量、用量、结束
 *
 * 内部消息格式沿用 OpenAI Chat 的形状，因为内核各层都按这个形状写好了。
 */

export interface ToolCall {
  id: string;
  type: "function";
  function: { name: string; arguments: string };
}

export interface Message {
  role: "system" | "user" | "assistant" | "tool";
  content?: string | null;
  tool_calls?: ToolCall[];
  tool_call_id?: string;
  [key: string]: unknown;
}

/** 适配器产出的、可直接发出去的请求 */
export interface HttpRequest {
  url: string;
  headers: Record<string, string>;
  body: string;
}

/** 一次请求的全部输入 */
export interface RequestContext {
  modelId: string;
  messages: Message[];
  tools: Array<Record<string, unknown>> | null;
  temperature: number;
  maxTokens: number;
  stream: boolean;
  apiKey: string;
  baseUrl: string;
  quirks: Record<string, unknown>;
}

/** 去掉尾斜杠的接口地址：协议层拼 URL 统一从这里拿 */
export function trimBase(url: string): string {
  return url.replace(/\/+$/, "");
}

// ---------------------------------------------------------------- 事件

export interface TextDelta {
  kind: "text";
  text: string;
}

export interface ToolCallDelta {
  kind: "tool_call";
  index: number;
  id?: string;
  name?: string;
  argumentsDelta?: string;
}

export interface Usage {
  kind: "usage";
  inputTokens: number;
  outputTokens: number;
}

export interface Finished {
  kind: "finished";
  reason: string;
}

export type StreamEvent = TextDelta | ToolCallDelta | Usage | Finished;

/** 把供应商的 SSE 翻译成统一事件 */
export interface StreamParser {
  /** 喂一整块文本（可以是不完整的行），返回已解析的事件 */
  feed(chunk: string): StreamEvent[];
  /** 流结束时补上未闭合的行 */
  flush?(): StreamEvent[];
}

export interface Protocol {
  name: string;
  supportsTools: boolean;
  build(ctx: RequestContext): HttpRequest;
  parser(): StreamParser;
}

/** 便于适配器复用的鉴权头 */
export function bearer(apiKey: string): Record<string, string> {
  return { Authorization: `Bearer ${apiKey}`, "Content-Type": "application/json" };
}

/** 按 SSE 行切分：喂进来的 chunk 可能切在行中间，所以要留住尾巴 */
export class SseBuffer {
  private pending = "";

  /** 返回本次可处理的完整行 */
  take(chunk: string): string[] {
    this.pending += chunk;
    const parts = this.pending.split(/\r?\n/);
    this.pending = parts.pop() ?? "";
    return parts;
  }

  /** 流结束：把残留当最后一行吐出来 */
  drain(): string[] {
    const rest = this.pending.trim();
    this.pending = "";
    return rest ? [rest] : [];
  }
}

/** 通用 SSE 外壳：解析 data 行，交给 onData */
export abstract class SseParser implements StreamParser {
  private readonly buffer = new SseBuffer();
  private event = "";

  feed(chunk: string): StreamEvent[] {
    const out: StreamEvent[] = [];
    for (const line of this.buffer.take(chunk)) out.push(...this.line(line));
    return out;
  }

  flush(): StreamEvent[] {
    const out: StreamEvent[] = [];
    for (const line of this.buffer.drain()) out.push(...this.line(line));
    return out;
  }

  private line(raw: string): StreamEvent[] {
    const text = raw.trim();
    if (!text || text.startsWith(":")) return [];
    if (text.startsWith("event:")) {
      this.event = text.slice(6).trim();
      return [];
    }
    if (!text.startsWith("data:")) return [];
    const data = text.slice(5).trim();
    if (!data || data === "[DONE]") return [];
    let payload: Record<string, unknown>;
    try {
      payload = JSON.parse(data) as Record<string, unknown>;
    } catch {
      return [];
    }
    return this.onData(payload, this.event);
  }

  abstract onData(payload: Record<string, unknown>, event: string): StreamEvent[];
}
