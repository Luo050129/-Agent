/** 会话搜索：命中、打分排序、标题加权、角色过滤、空查询、当前会话内搜索。 */

import assert from "node:assert/strict";
import { test } from "node:test";
import { mkdtempSync, rmSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { SessionStore } from "../src/core/persistence.ts";
import { Session } from "../src/core/session.ts";
import { formatHit, searchMessages, searchSessions } from "../src/core/search.ts";

function seed(dir: string): void {
  const first = new SessionStore(dir, "20260101-100000-aaaa");
  const s1 = new Session("提示");
  first.attach(s1);
  s1.addUser("帮我重构循环结构，去掉重复的熔断判断");
  s1.addAssistant("好的，先把 guard 抽出来");

  const second = new SessionStore(dir, "20260102-100000-bbbb");
  const s2 = new Session("提示");
  second.attach(s2);
  s2.addUser("写一个周报");
  s2.addAssistant("本周做了熔断和沙箱两件事");
}

test("跨会话命中关键词", () => {
  const dir = mkdtempSync(join(tmpdir(), "xu-search-"));
  try {
    seed(dir);
    const hits = searchSessions("熔断", { dir });
    assert.ok(hits.length >= 2, `应命中多个会话，实际 ${hits.length}`);
    assert.ok(hits.every((h) => h.snippet.includes("熔断")));
  } finally {
    rmSync(dir, { recursive: true, force: true });
  }
});

test("打分排序：用户原话优先", () => {
  const dir = mkdtempSync(join(tmpdir(), "xu-search-"));
  try {
    seed(dir);
    const hits = searchSessions("熔断", { dir });
    assert.equal(hits[0]?.role, "user", JSON.stringify(hits.map((h) => [h.role, h.score])));
  } finally {
    rmSync(dir, { recursive: true, force: true });
  }
});

test("多关键词按命中数加权", () => {
  const dir = mkdtempSync(join(tmpdir(), "xu-search-"));
  try {
    seed(dir);
    const both = searchSessions("熔断 沙箱", { dir });
    const one = searchSessions("熔断", { dir });
    assert.ok(both.length > 0);
    assert.ok(both[0]!.score >= 1);
    assert.ok(one.length >= both.length, "单关键词命中面不会更窄");
  } finally {
    rmSync(dir, { recursive: true, force: true });
  }
});

test("角色过滤只留用户", () => {
  const dir = mkdtempSync(join(tmpdir(), "xu-search-"));
  try {
    seed(dir);
    const hits = searchSessions("熔断", { dir, roles: ["user"] });
    assert.ok(hits.length > 0);
    assert.ok(hits.every((h) => h.role === "user"));
  } finally {
    rmSync(dir, { recursive: true, force: true });
  }
});

test("空查询与无命中都返回空数组", () => {
  const dir = mkdtempSync(join(tmpdir(), "xu-search-"));
  try {
    seed(dir);
    assert.deepEqual(searchSessions("", { dir }), []);
    assert.deepEqual(searchSessions("   ", { dir }), []);
    assert.deepEqual(searchSessions("完全不存在的词xyz", { dir }), []);
  } finally {
    rmSync(dir, { recursive: true, force: true });
  }
});

test("目录不存在不报错", () => {
  assert.deepEqual(searchSessions("任意", { dir: join(tmpdir(), "xu-nope-dir-xyz") }), []);
});

test("limit 生效", () => {
  const dir = mkdtempSync(join(tmpdir(), "xu-search-"));
  try {
    seed(dir);
    const hits = searchSessions("熔断", { dir, limit: 1 });
    assert.equal(hits.length, 1);
  } finally {
    rmSync(dir, { recursive: true, force: true });
  }
});

test("当前会话内搜索", () => {
  const msgs = [
    { role: "system", content: "系统提示" },
    { role: "user", content: "把沙箱的白名单改成可配置" },
    { role: "assistant", content: "改好了，白名单从环境变量读" },
  ];
  const hits = searchMessages(msgs, "白名单");
  assert.equal(hits.length, 2);
  assert.ok(hits.every((h) => h.sessionId === "current"));
});

test("命中行能压成一行", () => {
  const line = formatHit({
    sessionId: "20260101-100000-aaaa",
    title: "t",
    role: "user",
    snippet: "…熔断判断…",
    score: 3,
    updated: 0,
  });
  assert.ok(line.includes("你"));
  assert.ok(line.includes("熔断"));
});
