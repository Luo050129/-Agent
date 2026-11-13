/** 工具层：执行器容错、读写边界、命令执行。 */

import assert from "node:assert/strict";
import { test } from "node:test";
import { mkdtempSync, rmSync, writeFileSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { execute, tagResult, truncate } from "../src/tools/index.ts";

test("未知工具报错但不抛", async () => {
  const out = await execute("no_such_tool", "{}");
  assert.ok(out.startsWith("[ERR]"));
  assert.ok(out.includes("未知工具"));
});

test("坏 JSON 参数被挡住", async () => {
  const out = await execute("read_file", "not json");
  assert.ok(out.startsWith("[ERR]"));
  assert.ok(out.includes("解析失败"));
});

test("参数不是对象也拦", async () => {
  const out = await execute("read_file", "[1,2]");
  assert.ok(out.startsWith("[ERR]"));
});

test("越界读被拒", async () => {
  const out = await execute("read_file", JSON.stringify({ path: "C:/Windows/win.ini" }));
  assert.ok(out.startsWith("[ERR]"), out);
  assert.ok(out.includes("范围之外"), out);
});

test("工作区内读写正常", async () => {
  const dir = mkdtempSync(join(tmpdir(), "xu-tool-"));
  const file = join(dir, "note.txt");
  try {
    writeFileSync(file, "第一行\n第二行", "utf8");
    // tmpdir 不在白名单里，所以这里用项目内的临时文件
    const inside = join(process.cwd(), ".agent", "ts_tool_probe.txt");
    const write = await execute("write_file", JSON.stringify({ path: inside, content: "探针内容" }));
    assert.ok(write.includes("已写入"), write);
    const read = await execute("read_file", JSON.stringify({ path: inside }));
    assert.ok(read.includes("探针内容"), read);
  } finally {
    rmSync(dir, { recursive: true, force: true });
    rmSync(join(process.cwd(), ".agent", "ts_tool_probe.txt"), { force: true });
  }
});

test("歧义词自动加 OK 豁免", () => {
  assert.ok(tagResult("文件不存在时返回空").startsWith("[OK]"));
  assert.equal(tagResult("[ERR] 已经失败"), "[ERR] 已经失败", "已有前缀不重复加");
  assert.equal(tagResult("一切正常"), "一切正常", "干净结果不加前缀");
});

test("超长输出被截断", () => {
  const out = truncate("x".repeat(9000), 1000);
  assert.ok(out.includes("已截断"));
  assert.ok(out.length < 1200);
});

test("shell 命令能跑", async () => {
  const out = await execute("run_shell", JSON.stringify({ command: "echo ts-ok" }));
  assert.ok(out.includes("ts-ok"), out);
  assert.ok(out.includes("exit_code=0"), out);
});

test("失败命令带 [ERR]", async () => {
  const out = await execute("run_shell", JSON.stringify({ command: "exit 3" }));
  assert.ok(out.startsWith("[ERR]"), out);
  assert.ok(out.includes("exit_code=3"), out);
});

test("run_node 能算数", async () => {
  const out = await execute("run_node", JSON.stringify({ code: "console.log(6 * 7)" }));
  assert.ok(out.includes("42"), out);
});
