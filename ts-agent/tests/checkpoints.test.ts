/** 检查点与撤销：新建删除、覆盖还原、链式回退、环形上限。 */

import assert from "node:assert/strict";
import { test } from "node:test";
import { existsSync, mkdtempSync, readFileSync, rmSync, writeFileSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";
import * as cp from "../src/tools/checkpoints.ts";

function setup(): { dir: string; ckpt: string } {
  const dir = mkdtempSync(join(tmpdir(), "xu-ckpt-"));
  return { dir, ckpt: join(dir, "checkpoints") };
}

test("新建文件的撤销 = 删除它", () => {
  const { dir, ckpt } = setup();
  try {
    const target = join(dir, "created.txt");
    const snap = cp.snapshot(target); // 先快照：此时文件还不存在
    writeFileSync(target, "agent 写的", "utf8");
    cp.save(snap, ckpt);
    assert.equal(cp.pendingCount(ckpt), 1);

    const result = cp.undoLast(ckpt);
    assert.ok(result.includes("已删除"), result);
    assert.equal(existsSync(target), false);
    assert.equal(cp.pendingCount(ckpt), 0);
  } finally {
    rmSync(dir, { recursive: true, force: true });
  }
});

test("覆盖已有文件的撤销 = 还原原内容", () => {
  const { dir, ckpt } = setup();
  try {
    const target = join(dir, "exist.txt");
    writeFileSync(target, "原始内容", "utf8");

    const snap = cp.snapshot(target);
    writeFileSync(target, "第一次覆盖", "utf8");
    cp.save(snap, ckpt);

    const result = cp.undoLast(ckpt);
    assert.ok(result.includes("已还原"), result);
    assert.equal(readFileSync(target, "utf8"), "原始内容");
  } finally {
    rmSync(dir, { recursive: true, force: true });
  }
});

test("链式回退 v3 → v0", () => {
  const { dir, ckpt } = setup();
  try {
    const target = join(dir, "chain.txt");
    writeFileSync(target, "v0", "utf8");
    for (const v of ["v1", "v2", "v3"]) {
      const snap = cp.snapshot(target);
      writeFileSync(target, v, "utf8");
      cp.save(snap, ckpt);
    }
    assert.equal(cp.pendingCount(ckpt), 3);
    cp.undoLast(ckpt);
    cp.undoLast(ckpt);
    cp.undoLast(ckpt);

    assert.equal(readFileSync(target, "utf8"), "v0");
    assert.equal(cp.pendingCount(ckpt), 0);
    assert.ok(cp.undoLast(ckpt).includes("没有可撤销"));
  } finally {
    rmSync(dir, { recursive: true, force: true });
  }
});

test("环形只留最近 20 个", () => {
  const { dir, ckpt } = setup();
  try {
    const target = join(dir, "ring.txt");
    for (let i = 0; i < 25; i += 1) {
      const snap = cp.snapshot(target);
      writeFileSync(target, `round ${i}`, "utf8");
      cp.save(snap, ckpt);
    }
    assert.equal(cp.pendingCount(ckpt), cp.MAX_CHECKPOINTS);
  } finally {
    rmSync(dir, { recursive: true, force: true });
  }
});

test("二进制文件不快照，不产生假检查点", () => {
  const { dir, ckpt } = setup();
  try {
    const target = join(dir, "blob.bin");
    writeFileSync(target, Buffer.from([0, 159, 146, 150]));
    assert.equal(cp.snapshot(target), null, "读不出文本就不该给检查点");
    cp.save(cp.snapshot(target), ckpt);
    assert.equal(cp.pendingCount(ckpt), 0);
  } finally {
    rmSync(dir, { recursive: true, force: true });
  }
});

test("不存在的文件快照标记 existed=false", () => {
  const { dir, ckpt } = setup();
  try {
    const snap = cp.snapshot(join(dir, "nope.txt"));
    assert.ok(snap);
    assert.equal(snap?.existed, false);
    assert.equal(snap?.content, null);
    assert.equal(cp.pendingCount(ckpt), 0, "snapshot 本身不落盘");
  } finally {
    rmSync(dir, { recursive: true, force: true });
  }
});

test("recent 新的在前", () => {
  const { dir, ckpt } = setup();
  try {
    const a = join(dir, "a.txt");
    const b = join(dir, "b.txt");
    writeFileSync(a, "1", "utf8");
    cp.save(cp.snapshot(a), ckpt);
    writeFileSync(b, "2", "utf8");
    cp.save(cp.snapshot(b), ckpt);

    const items = cp.recent(5, ckpt);
    assert.equal(items.length, 2);
    assert.ok(items[0]?.path.endsWith("b.txt"), JSON.stringify(items));
  } finally {
    rmSync(dir, { recursive: true, force: true });
  }
});
