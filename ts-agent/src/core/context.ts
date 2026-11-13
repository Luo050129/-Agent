/**
 * 上下文预算管理。对应 Python 版的 core/context.ts。
 *
 * 两件事：
 *   1. prepare —— 超预算时把老历史压成一条摘要，保住最近几轮
 *   2. compactToolResult —— 单条工具结果太长时留头留尾，中间标"已压缩"
 *
 * 摘要本身也算 token，所以要逐条剔除直到装得下。
 */

import { contextBudget } from "../config.ts";
import { estimateMessages, estimateText, takePrefix, takeSuffix } from "../tokens.ts";
import { clip } from "../tokens.ts";
import type { Message } from "../wire.ts";

export interface ContextStats {
  tokens: number;
  budget: number;
  dropped: number;
  ratio: number;
}

export interface PreparedContext {
  view: Message[];
  stats: ContextStats;
}

const SUMMARY_TOKENS = 160;

export class ContextManager {
  readonly budget: number;
  readonly toolResultTokens: number;

  constructor(budget: number = contextBudget(), toolResultTokens = 750) {
    this.budget = budget;
    this.toolResultTokens = toolResultTokens;
  }

  /** 单条工具结果太长就头尾保留、中间折叠 */
  compactToolResult(text: string): string {
    if (estimateText(text) <= this.toolResultTokens) return text;
    const head = takePrefix(text, Math.floor(this.toolResultTokens * 0.66));
    const tail = takeSuffix(text, Math.floor(this.toolResultTokens * 0.34));
    return `${head}\n\n...（已压缩 ${estimateText(text)} → ${this.toolResultTokens} tok，原 ${text.length} 字符）...\n\n${tail}`;
  }

  /** 装得下就原样返回，装不下就摘要 + 最近若干条 */
  prepare(messages: Message[]): PreparedContext {
    const total = estimateMessages(messages as unknown as Array<Record<string, unknown>>);
    if (total <= this.budget) {
      return {
        view: messages.slice(),
        stats: { tokens: total, budget: this.budget, dropped: 0, ratio: total / this.budget },
      };
    }

    const system = messages[0]?.role === "system" ? messages[0] : null;
    const rest = system ? messages.slice(1) : messages.slice();

    // 从最新往回捡，预算留出摘要本身的位置
    const room = this.budget - (system ? estimateText(String(system.content ?? "")) : 0) - SUMMARY_TOKENS;
    const kept: Message[] = [];
    let used = 0;
    for (let i = rest.length - 1; i >= 0; i -= 1) {
      const msg = rest[i] as Message;
      const cost = estimateMessages([msg as unknown as Record<string, unknown>]);
      if (used + cost > room && kept.length > 0) break;
      kept.unshift(msg);
      used += cost;
    }

    const dropped = rest.length - kept.length;
    const summary = this.summarize(rest.slice(0, dropped));
    const view: Message[] = [];
    if (system) view.push(system);
    if (summary) view.push({ role: "system", content: summary });
    view.push(...this.repair(kept));

    const tokens = estimateMessages(view as unknown as Array<Record<string, unknown>>);
    return { view, stats: { tokens, budget: this.budget, dropped, ratio: tokens / this.budget } };
  }

  /** 被裁掉的历史压成规则摘要：谁问过什么、调过哪些工具、最后结论 */
  private summarize(dropped: Message[]): string {
    if (dropped.length === 0) return "";
    const asks = dropped
      .filter((m) => m.role === "user" && typeof m.content === "string")
      .map((m) => String(m.content).replace(/\s+/g, " ").slice(0, 40));
    const tools = dropped
      .filter((m) => m.role === "assistant" && Array.isArray(m.tool_calls))
      .flatMap((m) => (m.tool_calls ?? []).map((call) => call.function.name));
    const lastAnswer = [...dropped]
      .reverse()
      .find((m) => m.role === "assistant" && typeof m.content === "string" && m.content.trim());

    const lines = [`[更早的 ${dropped.length} 条历史已折叠]`];
    if (asks.length > 0) lines.push(`用户问过：${asks.slice(-6).join(" / ")}`);
    if (tools.length > 0) lines.push(`调用过工具：${[...new Set(tools)].join("、")}`);
    if (lastAnswer) lines.push(`当时的结论：${clip(String(lastAnswer.content), 80)}`);
    return takePrefix(lines.join("\n"), SUMMARY_TOKENS);
  }

  /** 修掉孤儿 tool 消息：tool 必须紧跟带 tool_calls 的 assistant */
  private repair(messages: Message[]): Message[] {
    const out: Message[] = [];
    for (const msg of messages) {
      if (msg.role === "tool") {
        const prev = out[out.length - 1];
        const paired =
          prev?.role === "assistant" &&
          Array.isArray(prev.tool_calls) &&
          prev.tool_calls.some((c) => c.id === msg.tool_call_id);
        if (!paired) continue;
      }
      out.push(msg);
    }
    return out;
  }
}
