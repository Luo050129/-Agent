/**
 * 终端事件渲染。对应 Python 版的 ui/console.ts。
 *
 * 内核只认 Sink 的九个方法，换界面（TUI / Web / JSON 管道）只换这一层。
 */

import { createInterface, type Interface } from "node:readline/promises";
import type { PlanStep } from "../core/plan.ts";
import type { Sink, TaskState } from "../core/loop.ts";

const useColor = process.stdout.isTTY === true;
const c = {
  dim: (s: string) => (useColor ? `\x1b[2m${s}\x1b[0m` : s),
  cyan: (s: string) => (useColor ? `\x1b[36m${s}\x1b[0m` : s),
  green: (s: string) => (useColor ? `\x1b[32m${s}\x1b[0m` : s),
  red: (s: string) => (useColor ? `\x1b[31m${s}\x1b[0m` : s),
  yellow: (s: string) => (useColor ? `\x1b[33m${s}\x1b[0m` : s),
};

export const color = c;

/**
 * 危险操作确认。有现成的 readline 就复用它（交互模式）；
 * 没有就临时开一个；非 TTY 直接拒绝 —— 管道里没人能回答。
 */
export async function confirmDanger(
  tool: string,
  args: string,
  rl?: Interface | null,
): Promise<boolean> {
  const detail = args.length > 240 ? `${args.slice(0, 240)}…` : args;

  if (!process.stdin.isTTY) {
    process.stdout.write(
      `
${c.yellow(`[安全模式] 非交互环境，已拒绝 ${tool}`)}（脚本里请加 --yes 明确放行）
`,
    );
    return false;
  }

  const own = rl ?? createInterface({ input: process.stdin, output: process.stdout });
  try {
    const answer = (
      await own.question(
        `
${c.yellow("⚠ 危险操作")} ${c.cyan(tool)}
  ${c.dim(detail)}
  执行吗？[y/N] `,
      )
    )
      .trim()
      .toLowerCase();
    return answer === "y" || answer === "yes";
  } finally {
    if (!rl) own.close();
  }
}

export class ConsoleSink implements Sink {
  lastState: TaskState | null = null;
  private streamed = false;
  private prompt: Interface | null = null;

  /** 交互模式把主 readline 交进来，确认时复用同一个 stdin */
  attachPrompt(rl: Interface | null): void {
    this.prompt = rl;
  }

  async confirm(tool: string, args: string): Promise<boolean> {
    return confirmDanger(tool, args, this.prompt);
  }

  text(piece: string): void {
    this.streamed = true;
    process.stdout.write(piece);
  }

  toolCall(name: string, args: string): void {
    process.stdout.write(`\n${c.cyan(`→ ${name}`)} ${c.dim(shorten(args, 120))}\n`);
  }

  toolResult(result: string): void {
    const firstLine = result.split("\n").slice(0, 2).join(" / ");
    process.stdout.write(`${c.dim(`  ${shorten(firstLine, 160)}`)}\n`);
  }

  state(snapshot: TaskState): void {
    this.lastState = snapshot;
  }

  error(message: string): void {
    process.stdout.write(`\n${c.red(message)}\n`);
  }

  notice(message: string): void {
    process.stdout.write(`\n${c.yellow(message)}\n`);
  }

  /** 计划清单：一次打印全部步骤 */
  plan(steps: PlanStep[]): void {
    this.streamed = false;
    process.stdout.write(`\n${c.cyan("计划")}（${steps.length} 步）\n`);
    steps.forEach((step, index) => {
      process.stdout.write(`  ${index + 1}. ${step.goal}\n     ${c.dim(`完成标准：${step.doneWhen}`)}\n`);
    });
    process.stdout.write("\n");
  }

  planStep(index: number, status: "running" | "done" | "failed", note?: string): void {
    const mark = status === "running" ? "▶" : status === "done" ? "✓" : "✗";
    const paint = status === "failed" ? c.red : status === "done" ? c.green : c.cyan;
    process.stdout.write(`${paint(`${mark} 第 ${index} 步`)}${note ? ` ${c.dim(note)}` : ""}\n`);
  }

  /** 收尾时打一行统计 */
  printState(): void {
    const st = this.lastState;
    if (!st) return;
    if (this.streamed) process.stdout.write("\n");
    this.streamed = false;
    const ratio = Math.round(st.contextTokens === 0 ? 0 : (st.contextTokens / st.contextBudget) * 100);
    const verified = st.verified ? " · 已验证" : "";
    process.stdout.write(
      c.dim(
        `上下文 ${ratio}%（${st.contextTokens}/${st.contextBudget} tok）· 工具 ${st.toolCalls} 次 · 完成度 ${Math.round(st.completion * 100)}%${verified}\n`,
      ),
    );
  }
}

function shorten(text: string, limit: number): string {
  const oneLine = text.replace(/\s+/g, " ").trim();
  return oneLine.length <= limit ? oneLine : `${oneLine.slice(0, limit)}…`;
}
