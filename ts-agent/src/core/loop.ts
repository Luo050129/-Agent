/**
 * 主循环。对应 Python 版的 core/loop.ts（先做 ReAct 那半，Plan 模式是下一步）。
 *
 * 一次循环干的事：
 *   准备上下文 → 问模型 → 要工具就执行并回灌 → 再问 … → 没有工具调用了就收尾
 * 每一步都问熔断「还允许吗」，任何终止路径都给人话报告。
 */

import { DANGEROUS, TOOL_SCHEMAS, execute } from "../tools/index.ts";
import type { Message, ToolCall } from "../wire.ts";
import { ContextManager } from "./context.ts";
import { Guard, isFailure } from "./guard.ts";
import { systemPrompt } from "./prompt.ts";
import { INTERNAL_PREFIXES, Session } from "./session.ts";

/** 只要实现 stream 就算模型，方便测试注入假模型 */
export interface Streamable {
  readonly label: string;
  readonly model: string;
  stream(
    messages: Message[],
    tools: Array<Record<string, unknown>> | null,
    onText?: (piece: string) => void,
  ): Promise<{ text: string; calls: ToolCall[] }>;
}

export interface TaskState {
  status: "planning" | "tooling" | "done" | "blocked";
  step: number;
  toolCalls: number;
  contextTokens: number;
  contextBudget: number;
  completion: number;
  note: string;
}

export interface Sink {
  text(piece: string): void;
  toolCall(name: string, args: string): void;
  toolResult(result: string): void;
  state(snapshot: TaskState): void;
  error(message: string): void;
  notice?(message: string): void;
}

/** 什么都不做的 Sink：测试和嵌入时用 */
export const NULL_SINK: Sink = {
  text() {},
  toolCall() {},
  toolResult() {},
  state() {},
  error() {},
};

export interface AgentOptions {
  maxSteps?: number;
  guard?: Guard;
  context?: ContextManager;
}

export class Agent {
  private readonly llm: Streamable;
  private readonly maxSteps: number;
  private readonly guard: Guard;
  private readonly context: ContextManager;

  constructor(llm: Streamable, opts: AgentOptions = {}) {
    this.llm = llm;
    this.maxSteps = opts.maxSteps ?? 20;
    this.guard = opts.guard ?? new Guard({ maxSteps: opts.maxSteps ?? 20 });
    this.context = opts.context ?? new ContextManager();
  }

  async run(session: Session, sink: Sink = NULL_SINK): Promise<string> {
    const started = Date.now();
    if (!session.messages.some((m) => m.role === "system")) {
      session.refreshSystem(systemPrompt());
    }

    let lastText = "";
    for (let step = 1; step <= this.maxSteps; step += 1) {
      session.steps = step;
      const { view, stats } = this.context.prepare(session.messages);
      this.emit(sink, {
        status: "planning",
        step,
        toolCalls: session.toolCalls,
        contextTokens: stats.tokens,
        contextBudget: stats.budget,
        completion: 0.4,
        note: "",
      });

      let text = "";
      let calls: ToolCall[] = [];
      try {
        const result = await this.llm.stream(view, TOOL_SCHEMAS, (piece) => sink.text(piece));
        text = result.text;
        calls = result.calls;
      } catch (err) {
        const message = `模型调用失败：${(err as Error).message}`;
        sink.error(message);
        return `这次没做成：${message}`;
      }

      if (calls.length === 0) {
        lastText = text;
        session.addAssistant(text);
        this.emit(sink, {
          status: "done",
          step,
          toolCalls: session.toolCalls,
          contextTokens: stats.tokens,
          contextBudget: stats.budget,
          completion: text.trim() ? 1 : 0.7,
          note: "",
        });
        return text;
      }

      // 先把这一轮的全部工具调用写进历史，再逐个执行 —— 协议要求 tool 消息配对
      session.addAssistant(text, calls);
      this.emit(sink, {
        status: "tooling",
        step,
        toolCalls: session.toolCalls,
        contextTokens: stats.tokens,
        contextBudget: stats.budget,
        completion: 0.6,
        note: "",
      });

      for (const call of calls) {
        const name = call.function.name;
        const rawArgs = call.function.arguments || "{}";
        const blocked = this.guard.beforeTool(name, rawArgs);
        if (blocked) {
          session.addTool(call.id, `被熔断拦截：${blocked}`);
          sink.toolResult(`被熔断拦截：${blocked}`);
          const report = this.guard.report(blocked);
          session.addAssistant(report);
          this.emit(sink, {
            status: "blocked",
            step,
            toolCalls: session.toolCalls,
            contextTokens: stats.tokens,
            contextBudget: stats.budget,
            completion: 0.2,
            note: blocked,
          });
          return report;
        }

        sink.toolCall(name, rawArgs);
        const raw = await execute(name, rawArgs);
        const result = DANGEROUS.has(name) ? this.context.compactToolResult(raw) : raw;
        sink.toolResult(result);
        session.addTool(call.id, result);
        this.guard.afterTool(name, rawArgs, result);
      }

      const stop = this.guard.checkStop();
      if (stop) {
        const report = this.guard.report(stop);
        session.addAssistant(report);
        this.emit(sink, {
          status: "blocked",
          step,
          toolCalls: session.toolCalls,
          contextTokens: stats.tokens,
          contextBudget: stats.budget,
          completion: 0.2,
          note: stop,
        });
        return report;
      }
    }

    const reason = `步数用尽（${this.maxSteps} 步）仍未收尾`;
    const report = this.guard.report(reason);
    sink.notice?.(report);
    session.addAssistant(report);
    this.emit(sink, {
      status: "blocked",
      step: this.maxSteps,
      toolCalls: session.toolCalls,
      contextTokens: 0,
      contextBudget: this.context.budget,
      completion: 0.3,
      note: reason,
    });
    return lastText || report;
  }

  private emit(sink: Sink, state: TaskState): void {
    sink.state(state);
  }
}

/** 界面展示时过滤内核自己塞进历史的消息 */
export function isInternal(text: string): boolean {
  return INTERNAL_PREFIXES.some((prefix) => text.startsWith(prefix));
}

export { isFailure };
