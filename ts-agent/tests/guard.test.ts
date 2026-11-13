/** 熔断：失败判定、重复调用、连续失败、人话报告。 */

import assert from "node:assert/strict";
import { test } from "node:test";
import { Guard, classify, isFailure } from "../src/core/guard.ts";

test("结构化前缀优先于关键词", () => {
  assert.equal(classify("[ERR] 读不到"), "error");
  assert.equal(classify("[OK] 文件不存在，这是正常的"), "ok");
  assert.equal(classify("普通内容"), "unknown");
  assert.equal(isFailure("[ERR] 失败"), true);
  assert.equal(isFailure("[OK] 失败是正常现象"), false, "[OK] 豁免不能被关键词推翻");
});

test("无前缀时按开头关键词兜底", () => {
  assert.equal(isFailure("文件不存在"), true);
  assert.equal(isFailure("一切正常"), false);
});

test("同一调用重复超限被拦", () => {
  const guard = new Guard({ maxSameCall: 2 });
  assert.equal(guard.beforeTool("list_dir", '{"path":"."}'), null);
  guard.afterTool("list_dir", '{"path":"."}', "ok");
  assert.equal(guard.beforeTool("list_dir", '{"path":"."}'), null);
  guard.afterTool("list_dir", '{"path":"."}', "ok");
  const blocked = guard.beforeTool("list_dir", '{"path":"."}');
  assert.ok(blocked && blocked.includes("重复"), String(blocked));
});

test("连续失败达到阈值就喊停", () => {
  const guard = new Guard({ maxFailStreak: 3, maxFailuresPerCall: 5 });
  for (let i = 0; i < 3; i += 1) {
    assert.equal(guard.beforeTool(`tool${i}`, "{}"), null);
    guard.afterTool(`tool${i}`, "{}", "[ERR] 执行异常");
  }
  const stop = guard.checkStop();
  assert.ok(stop && stop.includes("连续失败"), String(stop));
});

test("原地打转能识别", () => {
  const guard = new Guard({ maxUnproductive: 3, maxFailStreak: 99, maxSameCall: 99 });
  for (let i = 0; i < 3; i += 1) {
    guard.beforeTool(`t${i}`, "{}");
    guard.afterTool(`t${i}`, "{}", "同样的结果");
  }
  const stop = guard.checkStop();
  assert.ok(stop && stop.includes("原地打转"), String(stop));
});

test("报告给人话，不是异常", () => {
  const guard = new Guard();
  guard.beforeTool("x", "{}");
  guard.afterTool("x", "{}", "ok");
  const report = guard.report("测试原因");
  assert.ok(report.includes("测试原因"));
  assert.ok(report.includes("已经试过"));
});
