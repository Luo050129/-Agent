/**
 * 计划模式：先把任务拆成可验证的步骤，再逐步执行。
 * 对应 Python 版 core/loop.ts 的 _run_plan 与 parse_plan。
 *
 * 拆计划的硬要求：每步都要有 done_when（怎么算做完了）。
 * 没有验收标准的步骤，执行时无从判断对错，等于没拆。
 */

import type { Message } from "../wire.ts";

export type Mode = "react" | "plan";

export function normalizeMode(value: string | null | undefined): Mode {
  return value === "plan" ? "plan" : "react";
}

export interface PlanStep {
  goal: string;
  doneWhen: string;
}

export const MAX_PLAN_STEPS = 6;

export const PLAN_PROMPT = `先别动手。把这个任务拆成不超过 ${MAX_PLAN_STEPS} 步。
只输出一个 JSON 数组，不要别的解释：

[{"goal": "这一步要做什么", "done_when": "怎么验证这一步做完了"}]

要求：
- 每步都要能用工具验证，不要写"确认一下""再看看"这种含糊步骤
- 能一步做完就只给一步，不要凑数
- done_when 要具体到看什么（文件内容/命令输出/测试结果）`;

export const STEP_PROMPT = (step: PlanStep, index: number, total: number, done: string[]): string =>
  [
    `第 ${index}/${total} 步：${step.goal}`,
    `完成标准：${step.doneWhen}`,
    done.length > 0 ? `已经完成：${done.map((d, i) => `${i + 1}) ${d}`).join("；")}` : "",
    "现在动手做这一步。做完用一句话说明结果；如果发现计划本身有问题，直接说清楚卡在哪。",
  ]
    .filter(Boolean)
    .join("\n");

/** 从模型回复里抠出步骤清单。兼容纯 JSON、```json 代码块、前后带废话三种情况 */
export function parsePlan(text: string): PlanStep[] {
  const candidates: string[] = [];
  const fence = /```(?:json)?\s*([\s\S]*?)```/i.exec(text);
  if (fence?.[1]) candidates.push(fence[1]);
  const start = text.indexOf("[");
  const end = text.lastIndexOf("]");
  if (start !== -1 && end > start) candidates.push(text.slice(start, end + 1));
  candidates.push(text);

  for (const candidate of candidates) {
    let parsed: unknown;
    try {
      parsed = JSON.parse(candidate.trim());
    } catch {
      continue;
    }
    const list = Array.isArray(parsed) ? parsed : (parsed as { steps?: unknown })?.steps;
    if (!Array.isArray(list)) continue;

    const steps: PlanStep[] = [];
    for (const raw of list) {
      if (!raw || typeof raw !== "object") continue;
      const item = raw as Record<string, unknown>;
      const goal = String(item["goal"] ?? item["step"] ?? item["task"] ?? "").trim();
      if (!goal) continue;
      const doneWhen = String(item["done_when"] ?? item["doneWhen"] ?? item["verify"] ?? "看到明确结果").trim();
      steps.push({ goal, doneWhen });
      if (steps.length >= MAX_PLAN_STEPS) break;
    }
    if (steps.length > 0) return steps;
  }
  return [];
}

/** 判断一步是否卡住：这一步里出现过失败、且模型自己说没做成 */
export function looksStuck(results: string[]): boolean {
  if (results.length === 0) return false;
  const failures = results.filter((r) => r.startsWith("[ERR]")).length;
  return failures >= 2 || failures === results.length;
}

/** 重规划的提示：带上已完成的步骤和卡点 */
export function replanPrompt(stuck: string, done: string[], remaining: number): string {
  return [
    `计划执行到一半卡住了：${stuck}`,
    done.length > 0 ? `已经完成：${done.map((d, i) => `${i + 1}) ${d}`).join("；")}` : "",
    `请重新给出剩下的步骤（最多 ${remaining} 步），同样只输出 JSON 数组。`,
  ]
    .filter(Boolean)
    .join("\n");
}

/** 计划模式的收尾：把过程压成结论 */
export function planSummary(done: string[], stuck: string | null): string {
  const lines = ["计划执行完毕："];
  done.forEach((item, index) => lines.push(`${index + 1}. ${item}`));
  if (stuck) lines.push(`\n未完成：${stuck}`);
  return lines.join("\n");
}

export type { Message };
