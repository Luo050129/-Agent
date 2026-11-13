/** 计划模式：JSON 抠取、步骤上限、重规划提示、收尾总结。 */

import assert from "node:assert/strict";
import { test } from "node:test";
import { MAX_PLAN_STEPS, looksStuck, parsePlan, planSummary, replanPrompt } from "../src/core/plan.ts";

test("纯 JSON 数组直接解析", () => {
  const steps = parsePlan('[{"goal":"读文件","done_when":"看到内容"}]');
  assert.equal(steps.length, 1);
  assert.equal(steps[0]?.goal, "读文件");
  assert.equal(steps[0]?.doneWhen, "看到内容");
});

test("从 ```json 代码块里抠", () => {
  const text = '好的，计划如下：\n```json\n[{"goal":"跑测试","done_when":"exit_code=0"}]\n```\n就这样。';
  const steps = parsePlan(text);
  assert.equal(steps.length, 1);
  assert.equal(steps[0]?.goal, "跑测试");
});

test("前后带废话也能抠出数组", () => {
  const text = '我先想一下 {"a":1} 然后：[{"goal":"改配置","done_when":"读回确认"}] 完成';
  const steps = parsePlan(text);
  assert.equal(steps[0]?.goal, "改配置");
});

test("兼容 steps 包装与别名", () => {
  const steps = parsePlan('{"steps":[{"step":"第一步","verify":"看到输出"}]}');
  assert.equal(steps[0]?.goal, "第一步");
  assert.equal(steps[0]?.doneWhen, "看到输出");
});

test("超过上限被截断", () => {
  const many = JSON.stringify(Array.from({ length: 12 }, (_, i) => ({ goal: `步骤${i}` })));
  assert.equal(parsePlan(many).length, MAX_PLAN_STEPS);
});

test("没有 goal 的步骤被丢弃，全空就返回空", () => {
  assert.deepEqual(parsePlan('[{"done_when":"x"}]'), []);
  assert.deepEqual(parsePlan("完全不是 JSON"), []);
});

test("卡住判定：连续失败或全失败", () => {
  assert.equal(looksStuck(["[ERR] 失败", "[ERR] 又失败"]), true);
  assert.equal(looksStuck(["[ERR] 失败", "成功了"]), false);
  assert.equal(looksStuck([]), false);
});

test("重规划提示带已完成项", () => {
  const text = replanPrompt("测试不过", ["读代码"], 3);
  assert.ok(text.includes("测试不过"));
  assert.ok(text.includes("读代码"));
  assert.ok(text.includes("最多 3 步"));
});

test("收尾总结列出步骤与卡点", () => {
  const text = planSummary(["改 A", "改 B"], "C 步超时");
  assert.ok(text.includes("1. 改 A"));
  assert.ok(text.includes("未完成：C 步超时"));
});
