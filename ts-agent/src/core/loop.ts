/**
 * 主循环。对应 Python 版的 core/loop.ts。
 *
 * 两种模式共用同一套运行时：
 *   react —— 边做边看：问模型 → 要工具就执行回灌 → 再问，出口做验证
 *   plan  —— 先出可验证的步骤清单，再逐步执行，卡住重规划一次
 *
 * 每一步都问熔断「还允许吗」，任何终止路径都给人话报告。
 */

import { DANGEROUS, execute, toolSchemas } from "../tools/index.ts";
import type { Message, ToolCall } from "../wire.ts";
import { CompletionJudge, type VerifyOutcome } from "./completion.ts";
import { ContextManager } from "./context.ts";
import { Guard, isFailure } from "./guard.ts";
import {
  PLAN_PROMPT,
  STEP_PROMPT,
  looksStuck,
  normalizeMode,
  parsePlan,
  planSummary,
  replanPrompt,
  type Mode,
  type PlanStep,
} from "./plan.ts";
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
  status: "planning" | "tooling" | "checking" | "done" | "blocked";
  mode: Mode;
  step: number;
  toolCalls: number;
  contextTokens: number;
  contextBudget: number;
  completion: number;
  verified: boolean;
  note: string;
}

export interface Sink {
  text(piece: string): void;
  toolCall(name: string, args: string): void;
  toolResult(result: string): void;
  state(snapshot: TaskState): void;
  error(message: string): void;
  notice?(message: string): void;
  plan?(steps: PlanStep[]): void;
  planStep?(index: number, status: "running" | "done" | "failed", note?: string): void;
  /** 安全模式下的确认；没有实现就视为拒绝（自动化场景更安全） */
  confirm?(tool: string, args: string): Promise<boolean>;
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
  mode?: Mode | string;
  guard?: Guard;
  context?: ContextManager;
  judge?: CompletionJudge;
  /** 出口验证开关（默认开；纯问答会自己走 NOVERIFY 放行） */
  verify?: boolean;
  /** 安全模式：危险工具执行前先问一句 */
  safe?: boolean;
}

/** react 循环的一次执行结果 */
interface ReactResult {
  text: string;
  blocked: string | null;
  verified: boolean;
}

export class Agent {
  private readonly llm: Streamable;
  private readonly maxSteps: number;
  private readonly mode: Mode;
  private readonly guard: Guard;
  private readonly context: ContextManager;
  private readonly judge: CompletionJudge;
  private readonly verifyEnabled: boolean;
  private readonly safe: boolean;

  constructor(llm: Streamable, opts: AgentOptions = {}) {
    this.llm = llm;
    this.maxSteps = opts.maxSteps ?? 20;
    this.mode = normalizeMode(opts.mode);
    this.guard = opts.guard ?? new Guard({ maxSteps: this.maxSteps });
    this.context = opts.context ?? new ContextManager();
    this.judge = opts.judge ?? new CompletionJudge();
    this.verifyEnabled = opts.verify ?? true;
    this.safe = opts.safe ?? false;
  }

  async run(session: Session, sink: Sink = NULL_SINK): Promise<string> {
    if (!session.messages.some((m) => m.role === "system")) {
      session.refreshSystem(systemPrompt());
    }
    return this.mode === "plan" ? this.runPlan(session, sink) : this.runReact(session, sink).then((r) => r.text);
  }

  // ------------------------------------------------------------ react

  private async runReact(
    session: Session,
    sink: Sink,
    overrides: { budget?: number; verify?: boolean } = {},
  ): Promise<ReactResult> {
    const budget = overrides.budget ?? this.maxSteps;
    const doVerify = overrides.verify ?? this.verifyEnabled;
    let verifyAttempts = 0;
    let lastText = "";

    for (let step = 1; step <= budget; step += 1) {
      session.steps += 1;
      const { view, stats } = this.context.prepare(session.messages);
      this.emit(sink, {
        status: "planning",
        step,
        toolCalls: session.toolCalls,
        tokens: stats.tokens,
        budget: stats.budget,
        completion: this.judge.heuristic({
          toolCalls: session.toolCalls,
          step,
          recentResults: [],
        }),
        verified: false,
        note: "",
      });

      let text = "";
      let calls: ToolCall[] = [];
      try {
        const result = await this.llm.stream(view, toolSchemas(), (piece) => sink.text(piece));
        text = result.text;
        calls = result.calls;
      } catch (err) {
        const message = `模型调用失败：${(err as Error).message}`;
        sink.error(message);
        return { text: `这次没做成：${message}`, blocked: message, verified: false };
      }

      if (calls.length === 0) {
        // 出口：能验证就先验证，拿不到证据就如实说未验证
        if (doVerify && verifyAttempts < 1 && session.toolCalls > 0) {
          this.emit(sink, {
            status: "checking",
            step,
            toolCalls: session.toolCalls,
            tokens: stats.tokens,
            budget: stats.budget,
            completion: 0.8,
            verified: false,
            note: "核实中",
          });

          const outcome: VerifyOutcome = await this.judge.verify(
            session.messages,
            toolSchemas(),
            (msgs, tools) => this.llm.stream(msgs, tools),
          );

          if (outcome.kind === "call") {
            session.addAssistant(text, outcome.calls);
            let failure: string | null = null;
            for (const call of outcome.calls) {
              const args = call.function.arguments || "{}";
              const blocked = this.guard.beforeTool(call.function.name, args);
              if (blocked) {
                failure = blocked;
                break;
              }
              if (!(await this.allowTool(call.function.name, args, sink))) {
                failure = "用户拒绝了验证操作";
                break;
              }
              sink.toolCall(call.function.name, args);
              const raw = await execute(call.function.name, args);
              const compacted = DANGEROUS.has(call.function.name)
                ? this.context.compactToolResult(raw)
                : raw;
              sink.toolResult(compacted);
              session.addTool(call.id, compacted);
              this.guard.afterTool(call.function.name, args, compacted);
              if (!failure && isFailure(compacted)) failure = compacted;
            }

            if (failure) {
              verifyAttempts += 1;
              session.addUser(
                `[验证未通过] 用来核实的操作失败了：${failure.replace(/\s+/g, " ").slice(0, 300)}\n` +
                  "请修复问题后重新交付，交付前再做一次能证明问题的验证。",
              );
              continue;
            }
            session.addAssistant(text);
            lastText = text;
            this.emit(sink, {
              status: "done",
              step,
              toolCalls: session.toolCalls,
              tokens: stats.tokens,
              budget: stats.budget,
              completion: 1,
              verified: true,
              note: "",
            });
            return { text, blocked: null, verified: true };
          }

          if (outcome.kind === "todo") {
            verifyAttempts += 1;
            session.addAssistant(text);
            session.addUser(`[继续完成] ${outcome.reason}`);
            continue;
          }
          // noverify：纯问答放行，但不标已验证
        }

        lastText = text;
        session.addAssistant(text);
        this.emit(sink, {
          status: "done",
          step,
          toolCalls: session.toolCalls,
          tokens: stats.tokens,
          budget: stats.budget,
          completion: text.trim() ? 1 : 0.7,
          verified: false,
          note: "",
        });
        return { text, blocked: null, verified: false };
      }

      // 先把整轮工具调用写进历史，再逐个执行 —— 协议要求 tool 消息与父消息配对
      session.addAssistant(text, calls);
      this.emit(sink, {
        status: "tooling",
        step,
        toolCalls: session.toolCalls,
        tokens: stats.tokens,
        budget: stats.budget,
        completion: 0.6,
        verified: false,
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
            tokens: stats.tokens,
            budget: stats.budget,
            completion: 0.2,
            verified: false,
            note: blocked,
          });
          return { text: report, blocked, verified: false };
        }

        if (!(await this.allowTool(name, rawArgs, sink))) {
          const denied = "[ERR] 用户拒绝了这次操作（安全模式）。换一个不动系统的做法，或说明为什么必须执行。";
          sink.toolResult(denied);
          session.addTool(call.id, denied);
          this.guard.afterTool(name, rawArgs, denied, true);
          continue;
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
          tokens: stats.tokens,
          budget: stats.budget,
          completion: 0.2,
          verified: false,
          note: stop,
        });
        return { text: report, blocked: stop, verified: false };
      }
    }

    const reason = `步数用尽（${budget} 步）仍未收尾`;
    const report = this.guard.report(reason);
    sink.notice?.(report);
    session.addAssistant(report);
    this.emit(sink, {
      status: "blocked",
      step: budget,
      toolCalls: session.toolCalls,
      tokens: 0,
      budget: this.context.budget,
      completion: 0.3,
      verified: false,
      note: reason,
    });
    return { text: lastText || report, blocked: reason, verified: false };
  }

  // ------------------------------------------------------------ plan

  private async runPlan(session: Session, sink: Sink): Promise<string> {
    let steps = await this.makePlan(session);
    if (steps.length === 0) {
      sink.notice?.("没拆出可执行的步骤，改为直接执行。");
      const fallback = await this.runReact(session, sink);
      return fallback.text;
    }

    sink.plan?.(steps);
    const done: string[] = [];
    let replanned = false;

    for (let index = 0; index < steps.length; index += 1) {
      const step = steps[index] as PlanStep;
      sink.planStep?.(index + 1, "running", step.goal.slice(0, 60));

      // 内核注入这一步的指令（历史里会带上标记，界面展示时过滤）
      session.addUser(STEP_PROMPT(step, index + 1, steps.length, done));

      const result = await this.runReact(session, sink, {
        budget: Math.max(3, this.maxSteps - session.steps),
        verify: false, // 整条计划的验证放在收尾
      });

      if (result.blocked) {
        sink.planStep?.(index + 1, "failed", result.text.slice(0, 60));
        if (!replanned) {
          replanned = true;
          const remaining = Math.max(1, steps.length - index);
          const fresh = await this.makePlan(session, replanPrompt(result.blocked, done, remaining));
          if (fresh.length > 0) {
            steps = fresh;
            index = -1; // 从头跑新计划（done 里已有的不会重复）
            sink.notice?.("计划卡住了，已重新规划。");
            sink.plan?.(steps);
            continue;
          }
        }
        return planSummary(done, result.blocked);
      }

      sink.planStep?.(index + 1, "done", result.text.slice(0, 60));
      done.push(step.goal);
    }

    // 整条计划收尾时做一次验证：拿得到证据才算真的做完
    if (this.verifyEnabled && session.toolCalls > 0) {
      const outcome = await this.judge.verify(session.messages, toolSchemas(), (msgs, tools) =>
        this.llm.stream(msgs, tools),
      );
      if (outcome.kind === "todo") {
        done.push(`（自检提示还差：${outcome.reason}）`);
      }
    }

    const summary = planSummary(done, null);
    session.addAssistant(summary);
    this.emit(sink, {
      status: "done",
      step: session.steps,
      toolCalls: session.toolCalls,
      tokens: 0,
      budget: this.context.budget,
      completion: 0.95,
      verified: false,
      note: "",
    });
    return summary;
  }

  /** 让模型出计划；失败返回空数组（调用方降级到直接执行） */
  private async makePlan(session: Session, extraPrompt?: string): Promise<PlanStep[]> {
    const messages: Message[] = [
      ...this.context.prepare(session.messages).view,
      { role: "user", content: extraPrompt ?? PLAN_PROMPT },
    ];
    try {
      const { text } = await this.llm.stream(messages, null); // 出计划时不给工具
      return parsePlan(text);
    } catch {
      return [];
    }
  }

  /** 危险工具是否需要用户点头；没实现 confirm 的界面按拒绝处理 */
  private async allowTool(name: string, args: string, sink: Sink): Promise<boolean> {
    if (!this.safe || !DANGEROUS.has(name)) return true;
    if (!sink.confirm) return false;
    try {
      return await sink.confirm(name, args);
    } catch {
      return false;
    }
  }

  // ------------------------------------------------------------ 状态

  private emit(
    sink: Sink,
    input: {
      status: TaskState["status"];
      step: number;
      toolCalls: number;
      tokens: number;
      budget: number;
      completion: number;
      verified: boolean;
      note: string;
    },
  ): void {
    sink.state({
      status: input.status,
      mode: this.mode,
      step: input.step,
      toolCalls: input.toolCalls,
      contextTokens: input.tokens,
      contextBudget: input.budget,
      completion: input.completion,
      verified: input.verified,
      note: input.note,
    });
  }
}

/** 界面展示时过滤内核自己塞进历史的消息 */
export function isInternal(text: string): boolean {
  return INTERNAL_PREFIXES.some((prefix) => text.startsWith(prefix));
}

export { isFailure, looksStuck };
