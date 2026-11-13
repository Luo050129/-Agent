/**
 * 终端事件渲染。对应 Python 版的 ui/console.ts。
 *
 * 内核只认 Sink 的九个方法，换界面（TUI / Web / JSON 管道）只换这一层。
 */

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

export class ConsoleSink implements Sink {
  lastState: TaskState | null = null;
  private streamed = false;

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

  /** 收尾时打一行统计 */
  printState(): void {
    const st = this.lastState;
    if (!st) return;
    if (this.streamed) process.stdout.write("\n");
    this.streamed = false;
    const ratio = Math.round(st.contextTokens === 0 ? 0 : (st.contextTokens / st.contextBudget) * 100);
    process.stdout.write(
      c.dim(
        `上下文 ${ratio}%（${st.contextTokens}/${st.contextBudget} tok）· 工具 ${st.toolCalls} 次 · 完成度 ${Math.round(st.completion * 100)}%\n`,
      ),
    );
  }
}

function shorten(text: string, limit: number): string {
  const oneLine = text.replace(/\s+/g, " ").trim();
  return oneLine.length <= limit ? oneLine : `${oneLine.slice(0, limit)}…`;
}
