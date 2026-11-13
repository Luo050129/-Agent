/**
 * 会话状态。对应 Python 版的 core/session.ts。
 *
 * 铁律：所有历史写入都走 append()。持久化、审计、状态统计都挂在它上面，
 * 旁路写入 = 漏记。
 */

import type { Message, ToolCall } from "../wire.ts";

/** 内核自己往历史里塞的消息前缀，界面展示时要过滤掉 */
export const INTERNAL_PREFIXES = ["[验证未通过]", "[内核对齐]", "[继续完成]"] as const;

export type Observer = (msg: Message) => void;

export class Session {
  messages: Message[] = [];
  steps = 0;
  toolCalls = 0;
  private observer: Observer | null = null;

  constructor(systemPrompt?: string) {
    if (systemPrompt) this.messages.push({ role: "system", content: systemPrompt });
  }

  get turns(): number {
    return this.messages.filter((m) => m.role === "user").length;
  }

  /** 唯一入库口：写消息、通知观察者 */
  append(msg: Message): void {
    this.messages.push(msg);
    if (msg.role === "assistant" && Array.isArray(msg.tool_calls)) {
      this.toolCalls += msg.tool_calls.length;
    }
    if (this.observer) {
      try {
        this.observer(msg);
      } catch {
        this.observer = null; // 观察者坏了就摘掉，不能拖累对话
      }
    }
  }

  /** 挂持久化观察者；返回解绑函数 */
  attachObserver(fn: Observer): () => void {
    this.observer = fn;
    return () => {
      this.observer = null;
    };
  }

  addUser(text: string): void {
    this.append({ role: "user", content: text });
  }

  addAssistant(text: string, calls?: ToolCall[]): void {
    const msg: Message = { role: "assistant", content: text };
    if (calls && calls.length > 0) msg.tool_calls = calls;
    this.append(msg);
  }

  addTool(callId: string, result: string): void {
    this.append({ role: "tool", tool_call_id: callId, content: result });
  }

  /** 换系统提示（装了插件 / 加了技能之后要刷新） */
  refreshSystem(text: string): void {
    if (this.messages[0]?.role === "system") this.messages[0] = { role: "system", content: text };
    else this.messages.unshift({ role: "system", content: text });
  }

  reset(): void {
    const system = this.messages[0]?.role === "system" ? this.messages[0] : null;
    this.messages = system ? [system] : [];
    this.steps = 0;
    this.toolCalls = 0;
  }
}
