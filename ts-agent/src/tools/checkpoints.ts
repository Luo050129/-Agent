/**
 * 编辑检查点与撤销。对应 Python 版 tools/checkpoints.ts。
 *
 * write_file 覆盖前先快照原内容，用户一条命令退回去。语义：
 *   - 之前存在 → 还原原内容；之前不存在 → 删掉它
 *   - 撤销成功后该检查点移除，再撤一次就是更早那次（链式回退）
 *   - 撤销失败时检查点保留，可以重试
 *   - 快照先读后落盘：真正的写入失败不会留下检查点
 *   - 二进制 / 读不出来的文件不快照 —— 宁可没有，不给假保证
 */

import { existsSync, mkdirSync, readFileSync, readdirSync, statSync, unlinkSync, writeFileSync } from "node:fs";
import { join } from "node:path";
import { AGENT_DIR } from "../config.ts";
import { ERR } from "../markers.ts";

export const CHECKPOINT_DIR = join(AGENT_DIR, "checkpoints");
export const MAX_CHECKPOINTS = 20;

export interface Snapshot {
  ts: string;
  path: string;
  existed: boolean;
  content: string | null;
}

function files(directory: string): string[] {
  if (!existsSync(directory)) return [];
  return readdirSync(directory)
    .filter((n) => n.endsWith(".json"))
    .sort()
    .map((n) => join(directory, n));
}

/** 读取目标当前状态；无法无损快照（二进制 / 读失败）返回 null */
export function snapshot(path: string): Snapshot | null {
  const existed = existsSync(path);
  if (!existed) {
    return { ts: new Date().toISOString(), path, existed: false, content: null };
  }
  try {
    const buf = readFileSync(path);
    // Node 读二进制不抛错（无效字节变成 U+FFFD），得自己判：
    // 有 NUL 字节或出现替换字符，就当二进制，不给"能还原"的假保证
    if (buf.includes(0)) return null;
    const content = buf.toString("utf8");
    if (content.includes("\uFFFD")) return null;
    return { ts: new Date().toISOString(), path, existed: true, content };
  } catch {
    return null; // 权限问题：不快照
  }
}

/** 落盘检查点；失败静默（检查点不能挡住正常写入） */
export function save(snap: Snapshot | null, directory: string = CHECKPOINT_DIR): string | null {
  if (!snap) return null;
  try {
    mkdirSync(directory, { recursive: true });
    const id = `${Date.now()}-${Math.random().toString(36).slice(2, 6)}`;
    writeFileSync(join(directory, `${id}.json`), JSON.stringify(snap), "utf8");
    prune(directory);
    return id;
  } catch {
    return null;
  }
}

function prune(directory: string): void {
  const all = files(directory);
  for (const old of all.slice(0, Math.max(0, all.length - MAX_CHECKPOINTS))) {
    try {
      unlinkSync(old);
    } catch {
      /* 忽略 */
    }
  }
}

/** 撤销最近一次写入，返回人话结果 */
export function undoLast(directory: string = CHECKPOINT_DIR): string {
  const all = files(directory);
  const latest = all[all.length - 1];
  if (!latest) return "没有可撤销的写入。";

  let snap: Snapshot;
  try {
    snap = JSON.parse(readFileSync(latest, "utf8")) as Snapshot;
  } catch {
    unlinkSync(latest);
    return `${ERR}检查点损坏，已跳过。再按一次撤销可回退更早的写入。`;
  }

  try {
    if (snap.existed) {
      if (snap.content === null) return `${ERR}该检查点没有内容快照，无法还原（${snap.path}）。`;
      writeFileSync(snap.path, snap.content, "utf8");
    } else {
      try {
        unlinkSync(snap.path);
      } catch {
        /* 文件本来就不在 */
      }
    }
  } catch (err) {
    return `${ERR}撤销失败：${(err as Error).message}（检查点保留，可重试）`;
  }

  try {
    unlinkSync(latest);
  } catch {
    /* 忽略 */
  }

  return snap.existed
    ? `已还原：${snap.path}（撤销 ${snap.ts} 的写入，恢复原内容 ${snap.content?.length ?? 0} 字符）`
    : `已删除：${snap.path}（它在该次写入前不存在）`;
}

export function pendingCount(directory: string = CHECKPOINT_DIR): number {
  return files(directory).length;
}

/** 最近的写入记录（新的在前），给界面展示 */
export function recent(limit = 8, directory: string = CHECKPOINT_DIR): Array<{ ts: string; path: string }> {
  return files(directory)
    .slice(-limit)
    .reverse()
    .map((f) => {
      try {
        const snap = JSON.parse(readFileSync(f, "utf8")) as Snapshot;
        return { ts: snap.ts, path: snap.path };
      } catch {
        return { ts: "", path: "" };
      }
    });
}

export { statSync };
