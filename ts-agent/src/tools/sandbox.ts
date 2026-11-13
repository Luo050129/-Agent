/**
 * 进程级沙箱。对应 Python 版 tools/sandbox.ts。
 *
 * 语言差异（必须先说清）：Python 版用 ctypes 直接调 Windows Job Object，
 * 能做到内核级进程树隔离 + 内存上限。Node 没有对应 API，AppContainer 和
 * restricted token 都要走 FFI 原生绑定（等于引入编译期依赖，破掉零依赖）。
 *
 * 所以 TS 版用三层组合，效果接近但不是同一种隔离：
 *   1. 堆上限    --max-old-space-size 限制 V8 堆（实测生效）
 *   2. 杀树      超时用 taskkill /T /F（Windows）或进程组 SIGKILL（POSIX）
 *   3. 守卫脚本  子进程内 patch fs / child_process / net，按白名单拦截
 *
 * 再加一道静态预检：`import { readFileSync } from "node:fs"` 这种命名导入
 * 拿到的是模块快照，patch 管不到 —— 所以入口就拒掉，要求用默认导入。
 */

import { spawn } from "node:child_process";
import { mkdtempSync, readFileSync, rmSync, writeFileSync } from "node:fs";
import { tmpdir } from "node:os";
import { dirname, isAbsolute, join, resolve } from "node:path";
import { fileURLToPath } from "node:url";
import { ERR } from "../markers.ts";
import { truncate } from "./util.ts";

const HERE = dirname(fileURLToPath(import.meta.url));

/** 读/写白名单，和 filesystem 用同一套环境变量 */
export function readRoots(): string[] {
  const extra = (process.env["AGENT_READ_ROOTS"] ?? "").split(";").map((p) => p.trim()).filter(Boolean);
  const roots = [...extra];
  for (const dir of [process.cwd(), resolve(HERE, "..", "..")]) {
    if (isAbsolute(dir)) roots.push(dir);
  }
  return [...new Set(roots.map((p) => resolve(p)))];
}

export function writeRoots(): string[] {
  const extra = (process.env["AGENT_WRITE_ROOTS"] ?? "").split(";").map((p) => p.trim()).filter(Boolean);
  return [...new Set([resolve(HERE, "..", ".."), ...extra.map((p) => resolve(p))])];
}

/** 沙箱守卫源码（独立 .mjs，便于阅读与测试） */
export function guardPrelude(): string {
  return readFileSync(join(HERE, "guard-prelude.mjs"), "utf8");
}

/** 命名导入会拿到模块快照，patch 拦不住 —— 入口直接拒绝 */
const NAMED_IMPORT_LEAK = [
  /import\s*\{[^}]*\}\s*from\s*["'](?:node:)?fs["']/,
  /import\s*\{[^}]*\}\s*from\s*["'](?:node:)?fs\/promises["']/,
];

/** 这些模块的静态 import 也绕不过守卫（ESM 不走 Module._load），入口就拒 */
const FORBIDDEN_MODULES =
  /from\s*["'](?:node:)?(?:child_process|cluster|net|tls|http|https|http2|dgram|dns|worker_threads)["']/;

/** 静态预检：返回拒绝原因，null 表示放行 */
export function staticCheck(code: string): string | null {
  for (const rule of NAMED_IMPORT_LEAK) {
    if (rule.test(code)) {
      return "请用默认导入 `import fs from \"node:fs\"`，命名导入会绕过沙箱守卫";
    }
  }
  if (FORBIDDEN_MODULES.test(code)) {
    return "沙箱内不能用 child_process / net / http 等模块；要跑命令请用 run_shell 工具";
  }
  return null;
}

export interface GuardedResult {
  code: number | null;
  stdout: string;
  stderr: string;
  timedOut: boolean;
}

export interface GuardedOptions {
  timeout?: number;
  cwd?: string;
  heapMb?: number;
  net?: boolean;
}

/** 在沙箱里跑一段 JS：守卫前缀 + 用户代码，写临时文件后单开进程 */
export async function runGuarded(code: string, opts: GuardedOptions = {}): Promise<GuardedResult> {
  const timeout = opts.timeout ?? 60;
  const heapMb = opts.heapMb ?? Number(process.env["XU_SANDBOX_HEAP_MB"] ?? 512);

  const dir = mkdtempSync(join(tmpdir(), "xu-sbx-"));
  const file = join(dir, "snippet.mjs");
  try {
    writeFileSync(file, `${guardPrelude()}\n\n${code}\n`, "utf8");

    const env: Record<string, string> = { ...process.env } as Record<string, string>;
    env["XU_SANDBOX_READ_ROOTS"] = readRoots().join(process.platform === "win32" ? ";" : ":");
    env["XU_SANDBOX_WRITE_ROOTS"] = writeRoots().join(process.platform === "win32" ? ";" : ":");
    if (opts.net) env["XU_SANDBOX_NET"] = "1";

    return await new Promise<GuardedResult>((done) => {
      const child = spawn(
        process.execPath,
        [`--max-old-space-size=${heapMb}`, file],
        {
          cwd: opts.cwd ?? process.cwd(),
          env,
          windowsHide: true,
        },
      );
      let stdout = "";
      let stderr = "";
      let timedOut = false;

      child.stdout?.on("data", (b: Buffer) => {
        if (stdout.length < 1_000_000) stdout += b.toString("utf8");
      });
      child.stderr?.on("data", (b: Buffer) => {
        if (stderr.length < 1_000_000) stderr += b.toString("utf8");
      });

      const killer = setTimeout(() => {
        timedOut = true;
        if (process.platform === "win32" && child.pid != null) {
          spawn("taskkill", ["/pid", String(child.pid), "/T", "/F"], { windowsHide: true });
        } else {
          child.kill("SIGKILL");
        }
      }, timeout * 1000);

      child.on("error", (err) => {
        clearTimeout(killer);
        done({ code: null, stdout, stderr: `${stderr}${err.message}`, timedOut: false });
      });
      child.on("close", (code) => {
        clearTimeout(killer);
        done({ code, stdout, stderr, timedOut });
      });
    });
  } finally {
    rmSync(dir, { recursive: true, force: true });
  }
}

/** 把沙箱结果格式化成工具输出 */
export function formatGuarded(result: GuardedResult, timeoutLabel: string): string {
  if (result.timedOut) return `${ERR}${timeoutLabel}（进程树已终止）`;
  const parts = [`${result.code === 0 ? "" : ERR}exit_code=${result.code}`];
  if (result.stdout.trim()) parts.push(`stdout:\n${result.stdout.trim()}`);
  if (result.stderr.trim()) parts.push(`stderr:\n${result.stderr.trim()}`);
  if (!result.stdout.trim() && !result.stderr.trim()) parts.push("（无输出）");
  return truncate(parts.join("\n"));
}
