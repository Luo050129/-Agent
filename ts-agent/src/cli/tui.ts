/**
 * 终端富界面。对应 Python 版 ui/window.ts 里「状态栏 + 计划清单」那部分的等价物。
 *
 * 为什么不是全屏 TUI：tkinter 那套在终端里的等价物是 Ink 或 raw-mode 渲染，
 * 前者要依赖，后者在 Windows 控制台 + 中文宽字符下容易错位。这里做的是
 * 「单行状态原地重绘 + 计划逐行追加」，稳定、可管道、复制粘贴不乱。
 */

import type { Sink, TaskState } from "../core/loop.ts";
import type { PlanStep } from "../core/plan.ts";
import { color } from "./sink.ts";

const useColor = process.stdout.isTTY === true;
const ESC = "\x1b[";

export class TuiSink implements Sink {
  lastState: TaskState | null = null;
  private modelLabel = "";
  private mode = "react";
  private steps: PlanStep[] = [];
  private streaming = false;
  private statusDirty = false;

  constructor(modelLabel: string, mode: string) {
    this.modelLabel = modelLabel;
    this.mode = mode;
  }

  /** 状态行：先清当前行再写，避免拉出长龙 */
  private drawStatus(): void {
    if (!useColor) return; // 不是终端就别玩光标
    const st = this.lastState;
    if (!st) return;
    const percent = Math.round((st.contextTokens / st.contextBudget) * 100);
    const bar = this.progressBar(st.completion);
    const verified = st.verified ? color.green(" ✓已核实") : "";
    const statusText =
      `${color.dim("模型")} ${this.modelLabel} ${color.dim("·")} ${color.cyan(st.mode)}` +
      ` ${color.dim("·")} 步 ${st.step} ${color.dim("·")} 工具 ${st.toolCalls}` +
      ` ${color.dim("·")} 上下文 ${percent}% ${color.dim("·")} ${bar} ${Math.round(st.completion * 100)}%${verified}`;
    process.stdout.write(`\r${ESC}2K${statusText}`);
    this.statusDirty = true;
  }

  private progressBar(value: number, width = 10): string {
    const filled = Math.max(0, Math.min(width, Math.round(value * width)));
    return `${color.cyan("█".repeat(filled))}${color.dim("░".repeat(width - filled))}`;
  }

  /** 输出正文前先把状态行让出来，避免正文接在状态行后面 */
  private breakLine(): void {
    if (this.statusDirty && useColor) {
      process.stdout.write("\n");
      this.statusDirty = false;
    }
  }

  text(piece: string): void {
    this.breakLine();
    this.streaming = true;
    process.stdout.write(piece);
  }

  toolCall(name: string, args: string): void {
    this.breakLine();
    process.stdout.write(`\n${color.cyan(`→ ${name}`)} ${color.dim(shorten(args, 100))}\n`);
  }

  toolResult(result: string): void {
    const head = result.split("\n").slice(0, 2).join(" / ");
    process.stdout.write(`${color.dim(`  ${shorten(head, 150)}`)}\n`);
  }

  state(snapshot: TaskState): void {
    this.lastState = snapshot;
    this.drawStatus();
  }

  error(message: string): void {
    this.breakLine();
    process.stdout.write(`${color.red(`\n${message}`)}\n`);
  }

  notice(message: string): void {
    this.breakLine();
    process.stdout.write(`\n${color.yellow(message)}\n`);
  }

  plan(steps: PlanStep[]): void {
    this.breakLine();
    this.steps = steps;
    process.stdout.write(`\n${color.cyan("计划")} ${color.dim(`（${steps.length} 步）`)}\n`);
    steps.forEach((step, index) => {
      process.stdout.write(`  ${color.dim(`${index + 1}.`)} ${step.goal}\n`);
      process.stdout.write(`     ${color.dim(`完成标准：${step.doneWhen}`)}\n`);
    });
    process.stdout.write("\n");
  }

  planStep(index: number, status: "running" | "done" | "failed", note?: string): void {
    this.breakLine();
    const mark = status === "running" ? "▶" : status === "done" ? "✓" : "✗";
    const paint = status === "failed" ? color.red : status === "done" ? color.green : color.cyan;
    const label = this.steps[index - 1]?.goal ?? "";
    process.stdout.write(
      `${paint(`${mark} 第 ${index} 步`)} ${color.dim(shorten(note || label, 70))}\n`,
    );
  }

  /** 收尾：状态行留在最后一行，不再覆盖 */
  printState(): void {
    if (this.streaming) this.breakLine();
    if (this.statusDirty) {
      process.stdout.write("\n");
      this.statusDirty = false;
    }
    const st = this.lastState;
    if (!st) return;
    const percent = Math.round((st.contextTokens / st.contextBudget) * 100);
    const verified = st.verified ? " · 已核实" : "";
    process.stdout.write(
      color.dim(
        `上下文 ${percent}%（${st.contextTokens}/${st.contextBudget} tok）· 工具 ${st.toolCalls} 次 · 完成度 ${Math.round(st.completion * 100)}%${verified}\n`,
      ),
    );
  }
}

function shorten(text: string, limit: number): string {
  const one = text.replace(/\s+/g, " ").trim();
  return one.length <= limit ? one : `${one.slice(0, limit)}…`;
}
