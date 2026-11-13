/**
 * 熔断与限流。对应 Python 版的 core/guard.ts。
 *
 * 一个 Agent 跑飞通常是这几种：同一调用转圈、反复失败还硬试、无限刷工具、
 * 模型慢到天荒地老、被边界连拒还继续。这里把它们收成一张网。
 *
 * 收手不抛异常，而是产出一份人话报告 —— 用户要知道卡在哪，而不是一片空白。
 */

import { ERR_TAG, OK_TAG } from "../markers.ts";

export interface Limits {
  maxSteps: number;
  maxToolCalls: number;
  maxSeconds: number;
  maxSameCall: number;
  maxFailuresPerCall: number;
  maxUnproductive: number;
  maxFailStreak: number;
  maxDenials: number;
}

export const DEFAULT_LIMITS: Limits = {
  maxSteps: 20,
  maxToolCalls: 40,
  maxSeconds: 300,
  maxSameCall: 3,
  maxFailuresPerCall: 2,
  maxUnproductive: 4,
  maxFailStreak: 6,
  maxDenials: 3,
};

/** 兜底词表：只保留「出现在开头就基本等于失败」的强信号 */
const FAIL_MARKERS = [
  "执行异常",
  "参数不匹配",
  "超时",
  "失败",
  "不存在",
  "exit_code=1",
  "被规则拒绝",
  "被熔断拦截",
  "解析失败",
  "无法还原",
  "不支持",
];

/** 三态判定：error / ok / unknown（按结构化前缀） */
export function classify(result: string): "error" | "ok" | "unknown" {
  const head = (result ?? "").slice(0, 40).trimStart();
  if (head.startsWith(ERR_TAG)) return "error";
  if (head.startsWith(OK_TAG)) return "ok";
  return "unknown";
}

export function isFailure(result: string): boolean {
  const kind = classify(result);
  if (kind === "error") return true;
  if (kind === "ok") return false;
  const head = (result ?? "").slice(0, 200);
  return FAIL_MARKERS.some((mark) => head.includes(mark));
}

export class Guard {
  readonly limits: Limits;
  private readonly sameCall = new Map<string, number>();
  private readonly callFailures = new Map<string, number>();
  private failuresInARow = 0;
  private denials = 0;
  private lastResults: string[] = [];
  private startedAt = Date.now();
  private totalCalls = 0;

  constructor(limits: Partial<Limits> = {}) {
    this.limits = { ...DEFAULT_LIMITS, ...limits };
  }

  get elapsedSeconds(): number {
    return (Date.now() - this.startedAt) / 1000;
  }

  get calls(): number {
    return this.totalCalls;
  }

  /** 执行前问一句「还允许吗」。返回拦截原因，null 表示放行 */
  beforeTool(name: string, args: string): string | null {
    if (this.totalCalls >= this.limits.maxToolCalls) {
      return `工具调用已达上限 ${this.limits.maxToolCalls} 次`;
    }
    if (this.elapsedSeconds > this.limits.maxSeconds) {
      return `任务已超时 ${this.limits.maxSeconds}s`;
    }

    const key = `${name}|${args}`;
    const seen = (this.sameCall.get(key) ?? 0) + 1;
    this.sameCall.set(key, seen);
    if (seen > this.limits.maxSameCall) {
      return `同一调用重复 ${seen} 次（工具 ${name}），结果不会变，换个做法`;
    }

    const fails = this.callFailures.get(key) ?? 0;
    if (fails >= this.limits.maxFailuresPerCall) {
      return `同一调用已失败 ${fails} 次（工具 ${name}），不再重试`;
    }

    if (this.denials >= this.limits.maxDenials) {
      return `连续被规则拒绝 ${this.denials} 次，停下来说明限制`;
    }
    return null;
  }

  /** 执行后记账 */
  afterTool(name: string, args: string, result: string, denied = false): void {
    this.totalCalls += 1;
    const key = `${name}|${args}`;
    const failed = isFailure(result);
    if (failed) {
      this.callFailures.set(key, (this.callFailures.get(key) ?? 0) + 1);
      this.failuresInARow += 1;
    } else {
      this.failuresInARow = 0;
    }
    if (denied) this.denials += 1;
    this.lastResults.push(result);
    if (this.lastResults.length > 20) this.lastResults.shift();
    if (this.failuresInARow >= this.limits.maxFailStreak) {
      this.failStreakTripped = true;
    }
  }

  private failStreakTripped = false;

  /** 每步结束问一句「该不该收手」 */
  checkStop(): string | null {
    if (this.failStreakTripped) return `连续失败 ${this.failuresInARow} 次，工具链可能整体不可用`;
    if (this.elapsedSeconds > this.limits.maxSeconds) return `任务超时 ${this.limits.maxSeconds}s`;
    if (this.totalCalls >= this.limits.maxToolCalls) return `工具调用达上限 ${this.limits.maxToolCalls} 次`;
    if (this.denials >= this.limits.maxDenials) return `连续被拒绝 ${this.denials} 次`;

    // 原地打转：连续拿到相同结果
    const tail = this.lastResults.slice(-this.limits.maxUnproductive);
    if (tail.length === this.limits.maxUnproductive && new Set(tail).size === 1) {
      return `连续 ${tail.length} 次拿到相同结果，判定为原地打转`;
    }
    return null;
  }

  /** 人话收尾报告 */
  report(reason: string): string {
    return [
      "这次没做成，先停下来说清楚：",
      `- 停下的原因：${reason}`,
      `- 已经试过：${this.totalCalls} 次工具调用，用时 ${this.elapsedSeconds.toFixed(1)}s`,
      `- 下一步可以：把问题拆小一点重试，或直接把你知道的信息告诉我`,
    ].join("\n");
  }
}
