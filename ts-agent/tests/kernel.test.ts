/** 内核闭环：假模型驱动，验证工具回灌、熔断拦截、步数上限、上下文预算。 */

import assert from "node:assert/strict";
import { test } from "node:test";
import { ContextManager } from "../src/core/context.ts";
import { Guard } from "../src/core/guard.ts";
import { Agent, NULL_SINK, type Sink, type Streamable, type TaskState } from "../src/core/loop.ts";
import { Session } from "../src/core/session.ts";
import type { Message, ToolCall } from "../src/wire.ts";

function call(name: string, args: Record<string, unknown>, id = "c1"): ToolCall {
  return { id, type: "function", function: { name, arguments: JSON.stringify(args) } };
}

class ScriptLLM implements Streamable {
  readonly label = "script";
  readonly model = "script";
  turns = 0;
  private readonly replies: Array<{ text: string; calls: ToolCall[] }>;

  constructor(replies: Array<{ text: string; calls: ToolCall[] }>) {
    this.replies = replies;
  }

  async stream(
    _messages: Message[],
    _tools: Array<Record<string, unknown>> | null,
    onText?: (piece: string) => void,
  ): Promise<{ text: string; calls: ToolCall[] }> {
    this.turns += 1;
    const reply = this.replies.shift() ?? { text: "收尾", calls: [] };
    if (reply.text) onText?.(reply.text);
    return reply;
  }
}

class CaptureSink implements Sink {
  states: TaskState[] = [];
  results: string[] = [];
  errors: string[] = [];
  text(piece: string): void {
    void piece;
  }
  toolCall(): void {}
  toolResult(result: string): void {
    this.results.push(result);
  }
  state(snapshot: TaskState): void {
    this.states.push(snapshot);
  }
  error(message: string): void {
    this.errors.push(message);
  }
}

test("工具调用能回灌并收尾", async () => {
  const llm = new ScriptLLM([
    { text: "", calls: [call("list_dir", { path: "." })] },
    { text: "目录里有一堆文件", calls: [] },
  ]);
  const session = new Session();
  session.addUser("看看目录");
  const sink = new CaptureSink();

  const answer = await new Agent(llm, { maxSteps: 5 }).run(session, sink);

  assert.equal(answer, "目录里有一堆文件");
  assert.equal(session.toolCalls, 1);
  assert.ok(sink.results[0]?.includes("[DIR]") || sink.results[0]?.includes("[FILE]"));
  assert.ok(session.messages.some((m) => m.role === "tool"));
  assert.equal(sink.states.at(-1)?.status, "done");
});

test("越界写入被规则拦下，结果回灌", async () => {
  const llm = new ScriptLLM([
    { text: "", calls: [call("write_file", { path: "C:/Windows/evil.txt", content: "x" })] },
    { text: "被拒了，换个做法", calls: [] },
  ]);
  const session = new Session();
  session.addUser("写个文件");
  const sink = new CaptureSink();

  const answer = await new Agent(llm, { maxSteps: 5 }).run(session, sink);

  const toolMsg = session.messages.find((m) => m.role === "tool");
  assert.ok(String(toolMsg?.content).includes("[ERR]"), "越界必须失败");
  assert.ok(String(toolMsg?.content).includes("范围之外"));
  assert.equal(answer, "被拒了，换个做法");
});

test("撞到熔断会停并给报告", async () => {
  const same = call("list_dir", { path: "." });
  const llm = new ScriptLLM([
    { text: "", calls: [same] },
    { text: "", calls: [{ ...same, id: "c2" }] },
    { text: "", calls: [{ ...same, id: "c3" }] },
  ]);
  const session = new Session();
  session.addUser("转圈");
  const sink = new CaptureSink();

  const answer = await new Agent(llm, {
    maxSteps: 6,
    guard: new Guard({ maxSameCall: 1 }),
  }).run(session, sink);

  assert.ok(answer.includes("没做成"), answer);
  assert.ok(answer.includes("已经试过"));
  assert.equal(sink.states.at(-1)?.status, "blocked");
});

test("步数用尽也要收尾", async () => {
  const llm = new ScriptLLM([
    { text: "", calls: [call("list_dir", { path: "." }, "a")] },
    { text: "", calls: [call("list_dir", { path: "src" }, "b")] },
    { text: "", calls: [call("list_dir", { path: "tests" }, "c")] },
  ]);
  const session = new Session();
  session.addUser("一直调工具");
  const answer = await new Agent(llm, { maxSteps: 2 }).run(session, NULL_SINK);
  assert.ok(answer.includes("步数用尽"), answer);
});

test("超预算会折叠旧历史并保住最近几轮", () => {
  const ctx = new ContextManager(400, 100);
  const messages: Message[] = [{ role: "system", content: "系统提示" }];
  for (let i = 0; i < 30; i += 1) {
    messages.push({ role: "user", content: `第 ${i} 个问题` + "补充".repeat(40) });
    messages.push({ role: "assistant", content: "回答" + "细节".repeat(40) });
  }

  const { view, stats } = ctx.prepare(messages);
  assert.ok(stats.tokens <= 400, `实际 ${stats.tokens}`);
  assert.ok(stats.dropped > 0);
  assert.equal(view[0]?.role, "system");
  assert.ok(String(view[1]?.content).includes("已折叠"));
  assert.ok(String(view.at(-1)?.content).includes("细节"), "最新一条必须留下");
});

test("工具结果超限时头尾保留", () => {
  const ctx = new ContextManager(32000, 50);
  const long = "开头".repeat(100) + "中间内容".repeat(200) + "结尾标记";
  const out = ctx.compactToolResult(long);
  assert.ok(out.includes("已压缩"));
  assert.ok(out.startsWith("开头"));
  assert.ok(out.endsWith("结尾标记"));
});

test("裁剪后不留孤儿 tool 消息", () => {
  const ctx = new ContextManager(120, 50);
  const messages: Message[] = [
    { role: "system", content: "系统" },
    { role: "assistant", content: "", tool_calls: [call("list_dir", { path: "." })] },
    { role: "tool", tool_call_id: "c1", content: "结果" },
  ];
  const { view } = ctx.prepare(messages);
  const firstTool = view.findIndex((m) => m.role === "tool");
  if (firstTool > 0) {
    const prev = view[firstTool - 1];
    assert.ok(prev?.role === "assistant" && Array.isArray(prev.tool_calls), "tool 必须紧跟父消息");
  }
});
