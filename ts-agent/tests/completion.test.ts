/** 完成度：规则快判 + 验证探针的四种结果。 */

import assert from "node:assert/strict";
import { test } from "node:test";
import { CompletionJudge, VERIFY_PROMPT } from "../src/core/completion.ts";
import type { Message, ToolCall } from "../src/wire.ts";

const judge = new CompletionJudge();

function reply(text: string, calls: ToolCall[] = []) {
  return async () => ({ text, calls });
}

const msgs: Message[] = [{ role: "user", content: "做个任务" }];

test("没动工具时分数低", () => {
  assert.ok(judge.heuristic({ toolCalls: 0, step: 1, recentResults: [] }) <= 0.5);
});

test("工具都成功分数高", () => {
  assert.ok(judge.heuristic({ toolCalls: 2, step: 2, recentResults: ["ok", "done"] }) >= 0.6);
});

test("报错扣分", () => {
  const score = judge.heuristic({ toolCalls: 6, step: 3, recentResults: ["[ERR] 失败", "[ERR] 又失败"] });
  assert.ok(score <= 0.45, String(score));
});

test("分数被夹在合理区间", () => {
  const score = judge.heuristic({ toolCalls: 1, step: 9, recentResults: Array(20).fill("ok") });
  assert.ok(score <= 0.95 && score >= 0.1);
});

test("给验证调用就照单执行", async () => {
  const call: ToolCall = { id: "v1", type: "function", function: { name: "read_file", arguments: "{}" } };
  const outcome = await judge.verify(msgs, [], reply("", [call]));
  assert.equal(outcome.kind, "call");
  if (outcome.kind === "call") assert.equal(outcome.calls[0]?.id, "v1");
});

test("验证调用数量封顶", async () => {
  const calls = Array.from({ length: 8 }, (_, i) => ({
    id: `v${i}`,
    type: "function" as const,
    function: { name: "read_file", arguments: "{}" },
  }));
  const outcome = await judge.verify(msgs, [], reply("", calls));
  assert.equal(outcome.kind, "call");
  if (outcome.kind === "call") assert.equal(outcome.calls.length, 3);
});

test("TODO 判为没做完", async () => {
  const outcome = await judge.verify(msgs, [], reply("TODO: 还缺结论"));
  assert.equal(outcome.kind, "todo");
  if (outcome.kind === "todo") assert.ok(outcome.reason.includes("结论"));
});

test("NOVERIFY 放行", async () => {
  const outcome = await judge.verify(msgs, [], reply("NOVERIFY: 纯问答"));
  assert.equal(outcome.kind, "noverify");
  if (outcome.kind === "noverify") assert.equal(outcome.reason, "纯问答");
});

test("只说 DONE 不给证据 → 放行但无理由", async () => {
  const outcome = await judge.verify(msgs, [], reply("DONE"));
  assert.equal(outcome.kind, "noverify");
  if (outcome.kind === "noverify") assert.equal(outcome.reason, "");
});

test("探针抛错不阻塞交付", async () => {
  const outcome = await judge.verify(msgs, [], async () => {
    throw new Error("网络炸了");
  });
  assert.equal(outcome.kind, "noverify");
});

test("探针提示词带上工程约束", () => {
  assert.ok(VERIFY_PROMPT.includes("NOVERIFY"));
  assert.ok(VERIFY_PROMPT.includes("TODO"));
});
