/**
 * 完成度判定。对应 Python 版 core/completion.ts。
 *
 * 两层：
 *   快判 —— 纯规则打分，看工具是否报错、有没有产出，零成本
 *   验证 —— 不问模型「做完了吗」（同一个模型给自己盖章等于没盖章），
 *           而是让它给出一只能机械证明完成的工具调用，内核真执行、用结果说话
 *
 * 纯问答任务无法工具验证，允许 NOVERIFY 放行，但不给「已验证」标记。
 */

import type { Message, ToolCall } from "../wire.ts";
import { isFailure } from "./guard.ts";

export const VERIFY_PROMPT = `只做一件事：核实你刚才声称的任务是否真的完成了。不要凭记忆下结论。
给出一只能证明它的工具调用，例如：读回刚写的文件确认内容、跑一次测试或命令、列出目标目录确认产物存在。
如果这个任务不涉及工具产物（纯问答、纯解释），无法用工具验证，就只输出一行 NOVERIFY: 原因（20 字以内）。
没做完就输出 TODO: 还差什么。`;

/** 探针最多要几个验证调用，防止模型拿一堆调用烧预算 */
export const MAX_VERIFY_CALLS = 3;

export type VerifyOutcome =
  | { kind: "call"; calls: ToolCall[] }
  | { kind: "todo"; reason: string }
  | { kind: "noverify"; reason: string };

export interface HeuristicInput {
  toolCalls: number;
  step: number;
  recentResults: string[];
}

export class CompletionJudge {
  /** 规则快判：0.1 ~ 0.95 */
  heuristic(input: HeuristicInput): number {
    if (input.toolCalls === 0) return 0.4;
    const errors = input.recentResults.filter((text) => isFailure(text)).length;
    let score = 0.55 + (errors === 0 ? 0.15 : -0.12 * errors);
    if (input.step >= 2 && errors === 0) score += 0.1;
    return Math.max(0.1, Math.min(0.95, score));
  }

  /**
   * 循环出口验证。need 由调用方注入模型，便于测试替身。
   * 返回：验证调用 / 明确没做完 / 无法验证
   */
  async verify(
    messages: Message[],
    tools: Array<Record<string, unknown>> | null,
    probe: (
      messages: Message[],
      tools: Array<Record<string, unknown>> | null,
    ) => Promise<{ text: string; calls: ToolCall[] }>,
  ): Promise<VerifyOutcome> {
    const input: Message[] = [...messages, { role: "user", content: VERIFY_PROMPT }];
    let text = "";
    let calls: ToolCall[] = [];
    try {
      const result = await probe(input, tools);
      text = result.text ?? "";
      calls = result.calls ?? [];
    } catch {
      return { kind: "noverify", reason: "" }; // 探针失败不该影响交付
    }

    if (calls.length > 0) return { kind: "call", calls: calls.slice(0, MAX_VERIFY_CALLS) };

    const body = text.trim();
    const todo = /TODO[:：]?\s*([\s\S]+)/.exec(body);
    if (todo?.[1]) return { kind: "todo", reason: todo[1].trim().replace(/\s+/g, " ").slice(0, 80) };

    const nv = /NOVERIFY[:：]?\s*(.*)/i.exec(body);
    if (nv) return { kind: "noverify", reason: (nv[1] ?? "").trim().slice(0, 60) };

    // 只说 DONE 没给证据：按未验证放行，但不标记
    return { kind: "noverify", reason: "" };
  }
}
