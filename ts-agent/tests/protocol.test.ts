/** OpenAI 协议：请求形状与 SSE 解析（分块切行、tool_calls 累积）。 */

import assert from "node:assert/strict";
import { test } from "node:test";
import { openaiProtocol } from "../src/model/protocols/openai.ts";
import type { RequestContext, StreamEvent } from "../src/wire.ts";

function ctx(overrides: Partial<RequestContext> = {}): RequestContext {
  return {
    modelId: "deepseek-chat",
    messages: [{ role: "user", content: "你好" }],
    tools: null,
    temperature: 0.3,
    maxTokens: 100,
    stream: true,
    apiKey: "sk-test",
    baseUrl: "https://api.deepseek.com/v1",
    quirks: {},
    ...overrides,
  };
}

test("URL 与鉴权头", () => {
  const req = openaiProtocol.build(ctx());
  assert.equal(req.url, "https://api.deepseek.com/v1/chat/completions");
  assert.equal(req.headers["Authorization"], "Bearer sk-test");
});

test("地址已经带 /chat/completions 就不重复拼", () => {
  const req = openaiProtocol.build(ctx({ baseUrl: "https://x.com/v1/chat/completions" }));
  assert.equal(req.url, "https://x.com/v1/chat/completions");
});

test("带工具时下发 tools 与 tool_choice", () => {
  const req = openaiProtocol.build(
    ctx({ tools: [{ type: "function", function: { name: "t" } }] }),
  );
  const body = JSON.parse(req.body) as Record<string, unknown>;
  assert.equal(body["tool_choice"], "auto");
  assert.equal((body["tools"] as unknown[]).length, 1);
  assert.deepEqual(body["stream_options"], { include_usage: true });
});

test("SSE 分块切行也能解析", () => {
  const parser = openaiProtocol.parser();
  const events: StreamEvent[] = [];
  const frame = (data: unknown): string => `data: ${JSON.stringify(data)}\n\n`;

  const payload1 = frame({ choices: [{ delta: { content: "你" } }] });
  const payload2 = frame({ choices: [{ delta: { content: "好" }, finish_reason: "stop" }] });

  // 故意把第二帧切在中间喂进去
  events.push(...parser.feed(payload1 + payload2.slice(0, 20)));
  events.push(...parser.feed(payload2.slice(20)));

  const texts = events.filter((e) => e.kind === "text").map((e) => (e as { text: string }).text);
  assert.deepEqual(texts, ["你", "好"]);
  assert.ok(events.some((e) => e.kind === "finished"));
});

test("tool_calls 分片能拼成完整调用", () => {
  const parser = openaiProtocol.parser();
  const events: StreamEvent[] = [];
  const frame = (data: unknown): string => `data: ${JSON.stringify(data)}\n\n`;

  events.push(
    ...parser.feed(
      frame({
        choices: [
          {
            delta: {
              tool_calls: [
                { index: 0, id: "call_1", function: { name: "list_dir", arguments: '{"pa' } },
              ],
            },
          },
        ],
      }),
    ),
  );
  events.push(
    ...parser.feed(
      frame({ choices: [{ delta: { tool_calls: [{ index: 0, function: { arguments: 'th":"."}' } }] } }] }),
    ),
  );

  const deltas = events.filter((e) => e.kind === "tool_call");
  assert.equal(deltas.length, 2);
  const joined = deltas.map((e) => (e as { argumentsDelta?: string }).argumentsDelta ?? "").join("");
  assert.equal(joined, '{"path":"."}');
});

test("usage 事件能被识别", () => {
  const parser = openaiProtocol.parser();
  const events = parser.feed(
    `data: ${JSON.stringify({ choices: [], usage: { prompt_tokens: 10, completion_tokens: 5 } })}\n\n`,
  );
  const usage = events.find((e) => e.kind === "usage");
  assert.ok(usage);
  assert.equal((usage as { inputTokens: number }).inputTokens, 10);
});

test("忽略注释行与 [DONE]", () => {
  const parser = openaiProtocol.parser();
  assert.deepEqual(parser.feed(": keep-alive\n\n"), []);
  assert.deepEqual(parser.feed("data: [DONE]\n\n"), []);
});
