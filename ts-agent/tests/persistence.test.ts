/** 会话存档：落盘、恢复、崩溃修复、列表与恢复定位。 */

import assert from "node:assert/strict";
import { test } from "node:test";
import { appendFileSync, existsSync, mkdtempSync, readFileSync, rmSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { SessionStore } from "../src/core/persistence.ts";
import { Session } from "../src/core/session.ts";

function tempDir(): string {
  return mkdtempSync(join(tmpdir(), "xu-sessions-"));
}

test("首条消息才建文件，并自动取标题", () => {
  const dir = tempDir();
  try {
    const store = new SessionStore(dir, "s1");
    const session = new Session("系统提示");
    store.attach(session);
    assert.equal(existsSync(store.path), false, "没聊天就不该建文件");

    session.addUser("帮我重构一下循环结构");
    assert.equal(existsSync(store.path), true);
    assert.equal(store.title, "帮我重构一下循环结构");

    const first = JSON.parse(readFileSync(store.path, "utf8").split("\n")[0] as string);
    assert.equal(first.type, "meta");
    assert.equal(first.id, "s1");
  } finally {
    rmSync(dir, { recursive: true, force: true });
  }
});

test("恢复后消息完整，系统提示重建", () => {
  const dir = tempDir();
  try {
    const store = new SessionStore(dir, "s2");
    const session = new Session("旧系统提示");
    store.attach(session);
    session.addUser("问题 A");
    session.addAssistant("", [
      { id: "c1", type: "function", function: { name: "list_dir", arguments: "{}" } },
    ]);
    session.addTool("c1", "结果");
    session.addAssistant("答案 A");

    const restored = store.load();
    const roles = restored.messages.map((m) => m.role);
    assert.equal(roles[0], "system");
    assert.equal(restored.messages.filter((m) => m.role === "system").length, 1);
    assert.ok(restored.messages.some((m) => m.content === "问题 A"));
    assert.ok(restored.messages.some((m) => m.role === "tool" && m.tool_call_id === "c1"));
    assert.equal(restored.toolCalls, 1);
    assert.equal(restored.turns, 1);
  } finally {
    rmSync(dir, { recursive: true, force: true });
  }
});

test("尾部孤儿 tool 与被截断的调用被剥掉", () => {
  const dir = tempDir();
  try {
    const store = new SessionStore(dir, "s3");
    const session = new Session("提示");
    store.attach(session);
    session.addUser("任务");
    // 崩溃现场：assistant 说要调两个工具，只回来一个
    session.addAssistant("", [
      { id: "a1", type: "function", function: { name: "list_dir", arguments: "{}" } },
      { id: "a2", type: "function", function: { name: "list_dir", arguments: '{"path":"."}' } },
    ]);
    session.addTool("a1", "结果一");

    const restored = store.load();
    assert.ok(!restored.messages.some((m) => m.role === "tool"), "孤儿 tool 应被剥掉");
    assert.ok(restored.messages.some((m) => m.content === "任务"), "用户消息要留下");
  } finally {
    rmSync(dir, { recursive: true, force: true });
  }
});

test("list 按最近修改倒序，带轮数", () => {
  const dir = tempDir();
  try {
    const first = new SessionStore(dir, "old");
    const s1 = new Session("提示");
    first.attach(s1);
    s1.addUser("旧会话");

    const second = new SessionStore(dir, "new");
    const s2 = new Session("提示");
    second.attach(s2);
    s2.addUser("第一问");
    s2.addUser("第二问");

    const items = SessionStore.list(dir);
    assert.equal(items.length, 2);
    assert.equal(items[0]?.id, "new", "最新的在前");
    assert.equal(items[0]?.turns, 2);
    assert.equal(items[1]?.title, "旧会话");
  } finally {
    rmSync(dir, { recursive: true, force: true });
  }
});

test("resume 支持 last 与指定 id，找不到报错", () => {
  const dir = tempDir();
  try {
    const store = new SessionStore(dir, "only");
    const session = new Session("提示");
    store.attach(session);
    session.addUser("唯一会话");

    assert.equal(SessionStore.resume("last", dir).sessionId, "only");
    assert.equal(SessionStore.resume("only", dir).sessionId, "only");
    assert.throws(() => SessionStore.resume("nope", dir), /找不到会话/);
  } finally {
    rmSync(dir, { recursive: true, force: true });
  }
});

test("改标题后恢复能读到新标题", () => {
  const dir = tempDir();
  try {
    const store = new SessionStore(dir, "t1");
    const session = new Session("提示");
    store.attach(session);
    session.addUser("原始问题");
    store.setTitle("重构 loop 主循环");

    const reloaded = new SessionStore(dir, "t1");
    reloaded.load();
    assert.equal(reloaded.title, "重构 loop 主循环");
  } finally {
    rmSync(dir, { recursive: true, force: true });
  }
});

test("坏行不会毁掉整个存档", () => {
  const dir = tempDir();
  try {
    const store = new SessionStore(dir, "bad");
    const session = new Session("提示");
    store.attach(session);
    session.addUser("正常消息");

    appendFileSync(store.path, '{"type":"msg","role":"user","cont', "utf8"); // 半截行

    const restored = store.load();
    assert.ok(restored.messages.some((m) => m.content === "正常消息"));
  } finally {
    rmSync(dir, { recursive: true, force: true });
  }
});
