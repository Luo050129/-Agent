/** 沙箱：文件白名单、子进程与网络拦截、静态预检、堆上限、超时杀树。 */

import assert from "node:assert/strict";
import { test } from "node:test";
import { execute } from "../src/tools/index.ts";
import { formatGuarded, guardPrelude, readRoots, runGuarded, staticCheck } from "../src/tools/sandbox.ts";

const NOPE = process.platform === "win32" ? "C:/Windows/win.ini" : "/root/xu_probe.txt";

async function runJs(code: string, timeout = 30): Promise<string> {
  return execute("run_node", JSON.stringify({ code, timeout }));
}

test("正常代码照旧能跑", async () => {
  const out = await runJs("console.log(2 + 3 * 7)");
  assert.ok(out.includes("23"), out);
  assert.ok(out.includes("exit_code=0"), out);
});

test("能读写工作区", async () => {
  const code = [
    'import fs from "node:fs";',
    'fs.writeFileSync(".agent/_sb_ts.txt", "探针内容");',
    'console.log(fs.readFileSync(".agent/_sb_ts.txt", "utf8"));',
  ].join("\n");
  const out = await runJs(code);
  assert.ok(out.includes("探针内容"), out);
});

test("越界读被拦", async () => {
  const out = await runJs(`import fs from "node:fs"; fs.readFileSync(${JSON.stringify(NOPE)});`);
  assert.ok(out.includes("[SANDBOX]"), out);
  assert.ok(out.includes("读取被沙箱拦截"), out);
});

test("越界写被拦", async () => {
  const target = process.platform === "win32" ? "C:/xu_sb_escape.txt" : "/root/xu_sb_escape.txt";
  const out = await runJs(`import fs from "node:fs"; fs.writeFileSync(${JSON.stringify(target)}, "x");`);
  assert.ok(out.includes("[SANDBOX]"), out);
  assert.ok(out.includes("写入"), out);
});

test("子进程被静态预检拦在入口", async () => {
  const out = await runJs('import cp from "node:child_process"; cp.execSync("echo hi");');
  assert.ok(out.startsWith("[ERR]"), out);
  assert.ok(out.includes("child_process") || out.includes("run_shell"), out);
});

test("运行期 require 子进程也被拦", async () => {
  const out = await runJs(
    'import { createRequire } from "node:module"; const r = createRequire(import.meta.url); r("node:child_process").execSync("echo hi");',
  );
  assert.ok(out.includes("[SANDBOX]"), out);
});

test("网络被拦", async () => {
  const code = [
    "try {",
    '  await fetch("https://example.com");',
    '  console.log("未拦住");',
    "} catch (e) {",
    '  console.log("拦住:", e.message);',
    "}",
  ].join("\n");
  const out = await runJs(code);
  assert.ok(out.includes("[SANDBOX]"), out);
  assert.ok(!out.includes("未拦住"), out);
});

test("命名导入会被静态拒绝并给出改法", () => {
  const reason = staticCheck('import { readFileSync } from "node:fs";\nreadFileSync("x");');
  assert.ok(reason !== null);
  assert.ok(reason?.includes("默认导入"), String(reason));
});

test("默认导入放行", () => {
  assert.equal(staticCheck('import fs from "node:fs";\nfs.writeFileSync("a.txt", "x");'), null);
});

test("超时会杀进程", async () => {
  const started = Date.now();
  const result = await runGuarded("await new Promise(r => setTimeout(r, 30000));", { timeout: 2 });
  const elapsed = (Date.now() - started) / 1000;
  assert.equal(result.timedOut, true);
  // 超时设的是 2s，只要远小于 30s 的 sleep 就说明真杀了；留足余量防机器忙时误报
  assert.ok(elapsed < 15, `应快速返回，实际 ${elapsed}s`);
  assert.ok(formatGuarded(result, "超时").includes("进程树已终止"));
});

test("堆上限参数真的生效", async () => {
  const code = [
    'import v8 from "node:v8";',
    "console.log(Math.round(v8.getHeapStatistics().heap_size_limit / 1048576));",
  ].join("\n");
  const result = await runGuarded(code, { timeout: 20, heapMb: 256 });
  const limit = Number(result.stdout.trim());
  assert.ok(limit > 0 && limit < 600, `堆上限应被限制在 600MB 以内，实际 ${limit}`);
});

test("守卫源码能被解析", () => {
  const prelude = guardPrelude();
  assert.ok(prelude.includes("XU_SANDBOX_READ_ROOTS"));
  assert.ok(prelude.length > 500);
});

test("白名单根目录包含项目根", () => {
  const roots = readRoots();
  assert.ok(roots.some((r) => r.toLowerCase().includes("ts-agent")), roots.join(" | "));
});
