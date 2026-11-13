/**
 * 执行命令和跑代码。对应 Python 版的 tools/runtime.ts。
 *
 * 语言差异：Python 版有 run_python，TS 版给 run_node —— 用 Node 自己算数，
 * 不再依赖本机装没装 Python。
 *
 * 超时必须杀掉整棵进程树：shell 里再起子进程时，只 kill 父进程会留下孤儿。
 */

import { spawn } from "node:child_process";
import { ERR } from "../markers.ts";
import { formatGuarded, runGuarded, staticCheck } from "./sandbox.ts";
import { truncate } from "./util.ts";

export interface RunResult {
  code: number | null;
  stdout: string;
  stderr: string;
  timedOut: boolean;
}

/** 带超时的执行：超时杀整棵进程树 */
function run(input: {
  command: string;
  shell: boolean;
  cwd?: string;
  timeout: number;
}): Promise<RunResult> {
  return new Promise((resolvePromise) => {
    const child = spawn(input.command, {
      shell: input.shell,
      cwd: input.cwd ?? process.cwd(),
      windowsHide: true,
    });

    let stdout = "";
    let stderr = "";
    let timedOut = false;

    child.stdout?.on("data", (chunk: Buffer) => {
      if (stdout.length < 2_000_000) stdout += chunk.toString("utf8");
    });
    child.stderr?.on("data", (chunk: Buffer) => {
      if (stderr.length < 2_000_000) stderr += chunk.toString("utf8");
    });

    const killer = setTimeout(() => {
      timedOut = true;
      if (process.platform === "win32" && child.pid != null) {
        // Windows 没有进程组，用 taskkill /T 连子孙一起杀
        spawn("taskkill", ["/pid", String(child.pid), "/T", "/F"], { windowsHide: true });
      } else {
        child.kill("SIGKILL");
      }
    }, input.timeout * 1000);

    child.on("error", (err) => {
      clearTimeout(killer);
      resolvePromise({ code: null, stdout, stderr: `${stderr}${err.message}`, timedOut: false });
    });

    child.on("close", (code) => {
      clearTimeout(killer);
      resolvePromise({ code, stdout, stderr, timedOut });
    });
  });
}

function format(result: RunResult, timedOutLabel: string): string {
  if (result.timedOut) return `${ERR}${timedOutLabel}（进程树已终止）`;
  const parts = [`${result.code === 0 ? "" : ERR}exit_code=${result.code}`];
  if (result.stdout.trim()) parts.push(`stdout:\n${result.stdout.trim()}`);
  if (result.stderr.trim()) parts.push(`stderr:\n${result.stderr.trim()}`);
  if (!result.stdout.trim() && !result.stderr.trim()) parts.push("（无输出）");
  return truncate(parts.join("\n"));
}

export async function runShell(args: Record<string, unknown>): Promise<string> {
  const command = String(args["command"] ?? "").trim();
  if (!command) return `${ERR}缺少 command 参数`;
  const timeout = Number(args["timeout"] ?? 60);
  const cwd = args["cwd"] ? String(args["cwd"]) : undefined;
  const result = await run({ command, shell: true, cwd, timeout });
  return format(result, `命令超时（>${timeout}s）`);
}

export async function runNode(args: Record<string, unknown>): Promise<string> {
  const code = String(args["code"] ?? "");
  if (!code.trim()) return `${ERR}缺少 code 参数`;

  // 静态预检先拦：命名导入 / 直接引 child_process 会绕过运行期守卫
  const blocked = staticCheck(code);
  if (blocked) return `${ERR}${blocked}`;

  const timeout = Number(args["timeout"] ?? 60);
  const result = await runGuarded(code, { timeout });
  return formatGuarded(result, `JS 执行超时（>${timeout}s）`);
}

export const schemas: Array<Record<string, unknown>> = [
  {
    type: "function",
    function: {
      name: "run_shell",
      description:
        "执行一条 shell 命令（Windows 为 cmd.exe / PowerShell），用于查看系统信息、运行程序、git 操作。返回 exit_code 与 stdout/stderr。",
      parameters: {
        type: "object",
        properties: {
          command: { type: "string", description: "要执行的命令" },
          cwd: { type: "string", description: "工作目录，默认当前目录" },
          timeout: { type: "integer", description: "超时秒数，默认 60" },
        },
        required: ["command"],
      },
    },
  },
  {
    type: "function",
    function: {
      name: "run_node",
      description:
        "在独立进程执行一段 JavaScript（ESM），适合精确计算、数据处理、验证算法。console.log 的内容会返回。",
      parameters: {
        type: "object",
        properties: {
          code: { type: "string", description: "完整的 JavaScript 代码" },
          timeout: { type: "integer", description: "超时秒数，默认 60" },
        },
        required: ["code"],
      },
    },
  },
];

export const handlers = {
  run_shell: runShell,
  run_node: runNode,
};

export const dangerous = ["run_shell", "run_node"];
