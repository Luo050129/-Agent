/**
 * 模型层出口。对应 Python 版的 miniagent/model/client.py。
 *
 * 一套接口，背后是任意协议：
 *     const llm = new LLM("deepseek-chat");
 *     const { text, calls } = await llm.stream(messages, tools, (t) => process.stdout.write(t));
 *
 * 重试三条规矩（和 Python 版一致）：
 *   1. 只有 5xx / 429 / 网络错误 / 流停滞才重试，4xx 立刻失败
 *   2. 已经吐出文字就不重试，否则界面上会出现重复内容
 *   3. 重试成功要告诉用户，不偷偷重试
 */

import { ConfigError, apiKey, defaultBaseUrl, defaultModel, envFloat, envInt } from "../config.ts";
import { openaiProtocol } from "./protocols/openai.ts";
import type { Message, Protocol, StreamEvent, ToolCall } from "../wire.ts";

export class LLMError extends Error {
  readonly status?: number;

  constructor(message: string, status?: number) {
    super(message);
    this.name = "LLMError";
    this.status = status;
  }
}

/** 本地/内网地址（Ollama、vLLM、LM Studio）不需要 Key */
function isLocal(baseUrl: string): boolean {
  let host = "";
  try {
    host = new URL(baseUrl).hostname.toLowerCase();
  } catch {
    return false;
  }
  if (host === "localhost" || host === "127.0.0.1" || host === "::1" || host === "0.0.0.0") return true;
  return /^(10\.|192\.168\.|172\.(1[6-9]|2\d|3[01])\.)/.test(host);
}

const PROTOCOLS: Record<string, Protocol> = { openai: openaiProtocol };

export interface LLMOptions {
  baseUrl?: string;
  apiKey?: string;
  temperature?: number;
  maxTokens?: number;
  timeout?: number;
  deadline?: number;
  retries?: number;
}

export interface StreamResult {
  text: string;
  calls: ToolCall[];
}

export class LLM {
  readonly model: string;
  readonly label: string;
  readonly protocolName: string;
  readonly baseUrl: string;
  readonly protocol: Protocol;
  readonly temperature: number;
  readonly maxTokens: number;
  readonly timeout: number;
  readonly deadline: number;
  readonly retries: number;
  /** 上一次实际尝试了几次（>1 说明重试过），界面可以据此提示 */
  lastAttempts = 0;
  lastUsage: { inputTokens: number; outputTokens: number } | null = null;
  private readonly key: string;

  constructor(model?: string, opts: LLMOptions = {}) {
    this.label = model || defaultModel();
    this.model = this.label;
    this.protocolName = "openai";
    this.protocol = PROTOCOLS[this.protocolName] as Protocol;
    this.baseUrl = (opts.baseUrl || defaultBaseUrl()).replace(/\/+$/, "");
    this.temperature = opts.temperature ?? 0.3;
    this.maxTokens = opts.maxTokens ?? 4096;
    this.timeout = opts.timeout ?? envFloat("AGENT_LLM_TIMEOUT", 60);
    this.deadline = opts.deadline ?? envFloat("AGENT_LLM_DEADLINE", 180);
    this.retries = opts.retries ?? envInt("AGENT_LLM_RETRIES", 2);

    if (opts.apiKey) {
      this.key = opts.apiKey;
    } else {
      try {
        this.key = apiKey();
      } catch (err) {
        if (isLocal(this.baseUrl)) {
          this.key = "not-needed"; // 本地模型不需要 Key
        } else {
          throw err; // 远程服务缺 Key：启动即报错，别带着假 Key 发请求
        }
      }
    }
  }

  /** 便于排错：当前协议会把请求发到哪 */
  get url(): string {
    return this.protocol.build({
      modelId: this.model,
      messages: [],
      tools: null,
      temperature: this.temperature,
      maxTokens: this.maxTokens,
      stream: true,
      apiKey: this.key,
      baseUrl: this.baseUrl,
      quirks: {},
    }).url;
  }

  async stream(
    messages: Message[],
    tools: Array<Record<string, unknown>> | null = null,
    onText?: (piece: string) => void,
  ): Promise<StreamResult> {
    let lastError: unknown = null;
    for (let attempt = 0; attempt <= this.retries; attempt += 1) {
      this.lastAttempts = attempt + 1;
      try {
        return await this.once(messages, tools, onText);
      } catch (err) {
        lastError = err;
        if (!this.retryable(err, attempt)) break;
        await sleep(400 * 2 ** attempt);
      }
    }
    throw lastError instanceof Error ? lastError : new LLMError(String(lastError));
  }

  /** 能不能重试：状态码 + 是否已经吐过字 */
  private retryable(err: unknown, attempt: number): boolean {
    if (attempt >= this.retries) return false;
    if (err instanceof StreamedError) return false; // 已经吐字，重试会重复
    if (err instanceof LLMError) {
      if (err.status == null) return true; // 网络层错误
      return err.status >= 500 || err.status === 429;
    }
    return true;
  }

  private async once(
    messages: Message[],
    tools: Array<Record<string, unknown>> | null,
    onText?: (piece: string) => void,
  ): Promise<StreamResult> {
    const req = this.protocol.build({
      modelId: this.model,
      messages,
      tools,
      temperature: this.temperature,
      maxTokens: this.maxTokens,
      stream: true,
      apiKey: this.key,
      baseUrl: this.baseUrl,
      quirks: {},
    });

    let response: Response;
    try {
      response = await fetch(req.url, {
        method: "POST",
        headers: req.headers,
        body: req.body,
        signal: AbortSignal.timeout(this.deadline * 1000),
      });
    } catch (err) {
      throw new LLMError(`请求失败：${(err as Error).message}`);
    }

    if (!response.ok) {
      const body = await response.text().catch(() => "");
      throw new LLMError(`接口返回 ${response.status}：${body.slice(0, 400)}`, response.status);
    }
    if (!response.body) throw new LLMError("接口没有返回流式内容");

    const parser = this.protocol.parser();
    const reader = response.body.getReader();
    const decoder = new TextDecoder("utf-8");
    const calls = new Map<number, { id: string; name: string; arguments: string }>();
    let text = "";
    let emitted = false;

    try {
      for (;;) {
        const { done, value } = await reader.read();
        if (done) break;
        const chunk = decoder.decode(value, { stream: true });
        for (const event of parser.feed(chunk)) {
          this.handle(event, calls, (piece) => {
            text += piece;
            emitted = true;
            onText?.(piece);
          });
        }
      }
      for (const event of parser.flush?.() ?? []) {
        this.handle(event, calls, (piece) => {
          text += piece;
          emitted = true;
          onText?.(piece);
        });
      }
    } catch (err) {
      if (emitted) throw new StreamedError(`流中断：${(err as Error).message}`);
      throw new LLMError(`流中断：${(err as Error).message}`);
    } finally {
      reader.releaseLock();
    }

    return { text, calls: assembleCalls(calls) };
  }

  private handle(
    event: StreamEvent,
    calls: Map<number, { id: string; name: string; arguments: string }>,
    emitText: (piece: string) => void,
  ): void {
    if (event.kind === "text") {
      emitText(event.text);
      return;
    }
    if (event.kind === "usage") {
      this.lastUsage = { inputTokens: event.inputTokens, outputTokens: event.outputTokens };
      return;
    }
    if (event.kind === "tool_call") {
      const slot = calls.get(event.index) ?? { id: "", name: "", arguments: "" };
      if (event.id) slot.id = event.id;
      if (event.name) slot.name = event.name;
      if (event.argumentsDelta) slot.arguments += event.argumentsDelta;
      calls.set(event.index, slot);
    }
  }
}

/** 已经吐过字之后的失败：保留内容，不再重试 */
class StreamedError extends LLMError {}

function assembleCalls(
  calls: Map<number, { id: string; name: string; arguments: string }>,
): ToolCall[] {
  return [...calls.entries()]
    .sort((a, b) => a[0] - b[0])
    .filter(([, slot]) => slot.name)
    .map(([index, slot]) => ({
      id: slot.id || `call_${index}`,
      type: "function" as const,
      function: { name: slot.name, arguments: slot.arguments || "{}" },
    }));
}

function sleep(ms: number): Promise<void> {
  return new Promise((resolve) => setTimeout(resolve, ms));
}

export { ConfigError };
