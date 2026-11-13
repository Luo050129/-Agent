/** token 估算与截断。对应 Python 版的 tests/test_context.py 里的估算部分。 */

import assert from "node:assert/strict";
import { test } from "node:test";
import { clip, estimateMessages, estimateText, takePrefix, takeSuffix } from "../src/tokens.ts";

test("中文按字算", () => {
  assert.equal(estimateText("你好世界"), 4);
});

test("英文按 4 字符 1 token", () => {
  const n = estimateText("hello world");
  assert.ok(n >= 1 && n <= 4, `预期 1~4，实际 ${n}`);
});

test("空字符串为 0", () => {
  assert.equal(estimateText(""), 0);
});

test("消息列表有固定开销", () => {
  assert.ok(estimateMessages([{ role: "user", content: "你好" }]) > 2);
});

test("前缀截断不超预算", () => {
  const text = "每".repeat(500);
  const out = takePrefix(text, 100);
  assert.ok(estimateText(out) <= 100);
  assert.ok(out.length > 80, "不能截得太狠");
});

test("后缀截断取到尾部", () => {
  const text = "a".repeat(400) + "结尾标记";
  const out = takeSuffix(text, 20);
  assert.ok(out.endsWith("结尾标记"));
});

test("clip 在句末收尾", () => {
  const text = "第一句话说完了。第二句话很长" + "啊".repeat(200);
  const out = clip(text, 20);
  assert.ok(estimateText(out) <= 25);
  assert.ok(out.endsWith("…"));
});

test("够短就不动", () => {
  assert.equal(clip("很短", 100), "很短");
});
