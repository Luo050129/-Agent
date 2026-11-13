/**
 * 会话存档。对应 Python 版 core/persistence.ts。
 *
 * 格式：`.agent/sessions/{id}.jsonl`，一行一事件，**追加写**。
 * 为什么是 JSONL 而不是整份 JSON：崩在写一半时，前面的行仍然有效。
 *
 * 恢复时要做三件修正：
 *   1. 丢掉存档里的 system，用当前 systemPrompt() 重建（工具/技能可能变了）
 *   2. 剥掉尾部配不上对的 tool 消息（崩溃残留）
 *   3. steps / toolCalls 从消息重算
 */

import { appendFileSync, existsSync, mkdirSync, readFileSync, readdirSync, statSync, unlinkSync, writeFileSync } from "node:fs";
import { join } from "node:path";
import { AGENT_DIR } from "../config.ts";
import type { Message, ToolCall } from "../wire.ts";
import { systemPrompt } from "./prompt.ts";
import { Session } from "./session.ts";

export const SESSIONS_DIR = join(AGENT_DIR, "sessions");
const MAX_KEPT = 200; // 单文件最多保留的事件行，防止无限膨胀

export interface SessionInfo {
  id: string;
  title: string;
  turns: number;
  updated: number;
  path: string;
}

interface StoredMeta {
  type: "meta";
  id: string;
  ts: string;
  title: string;
}

interface StoredTitle {
  type: "title";
  title: string;
}

type StoredMessage = ({ type: "msg" } & Message) | StoredMeta | StoredTitle;

function newSessionId(): string {
  const now = new Date();
  const pad = (n: number): string => String(n).padStart(2, "0");
  const stamp = `${now.getFullYear()}${pad(now.getMonth() + 1)}${pad(now.getDate())}-${pad(now.getHours())}${pad(now.getMinutes())}${pad(now.getSeconds())}`;
  return `${stamp}-${Math.random().toString(36).slice(2, 6)}`;
}

function readLines(file: string): StoredMessage[] {
  if (!existsSync(file)) return [];
  const out: StoredMessage[] = [];
  for (const line of readFileSync(file, "utf8").split(/\r?\n/)) {
    if (!line.trim()) continue;
    try {
      out.push(JSON.parse(line) as StoredMessage);
    } catch {
      // 半截行（断电/崩溃）跳过，不影响前面的数据
    }
  }
  return out;
}

export class SessionStore {
  readonly directory: string;
  readonly sessionId: string;
  title = "";
  private bound: Session | null = null;
  private created = false;
  private wroteTitle = false;

  constructor(directory: string = SESSIONS_DIR, sessionId: string = newSessionId()) {
    this.directory = directory;
    this.sessionId = sessionId;
  }

  get path(): string {
    return join(this.directory, `${this.sessionId}.jsonl`);
  }

  /** 挂到会话上：之后每条消息都会落盘 */
  attach(session: Session): void {
    this.bound = session;
    session.attachObserver((msg) => this.append(msg));
  }

  /** 首条用户消息顺带取标题（不调模型，零成本） */
  private deriveTitle(msg: Message): void {
    if (this.title || this.wroteTitle || msg.role !== "user") return;
    const text = String(msg.content ?? "").trim().replace(/\s+/g, " ");
    if (!text) return;
    this.title = text.slice(0, 24);
    this.wroteTitle = true;
  }

  private ensureFile(): boolean {
    if (this.created) return true;
    try {
      mkdirSync(this.directory, { recursive: true });
      if (!existsSync(this.path)) {
        const meta: StoredMeta = {
          type: "meta",
          id: this.sessionId,
          ts: new Date().toISOString(),
          title: this.title,
        };
        writeFileSync(this.path, `${JSON.stringify(meta)}\n`, "utf8");
      }
      this.created = true;
      return true;
    } catch {
      return false; // 存档失败不能挡住正常对话
    }
  }

  append(msg: Message): void {
    this.deriveTitle(msg);
    if (!this.ensureFile()) return;
    try {
      appendFileSync(this.path, `${JSON.stringify({ type: "msg", ...msg })}\n`, "utf8");
    } catch {
      // 磁盘满 / 权限问题：静默放弃，不打断对话
    }
  }

  setTitle(title: string): void {
    this.title = title.slice(0, 60);
    this.wroteTitle = true;
    if (!this.ensureFile()) return;
    try {
      appendFileSync(this.path, `${JSON.stringify({ type: "title", title: this.title })}\n`, "utf8");
    } catch {
      /* 忽略 */
    }
  }

  /** 从存档恢复出一个可用会话（已挂好观察者） */
  load(): Session {
    const events = readLines(this.path);
    let title = "";
    const messages: Message[] = [];

    for (const event of events) {
      if (event.type === "meta") title = event.title || "";
      else if (event.type === "title") title = event.title || title;
      else if (event.type === "msg") {
        const { type: _type, ...msg } = event;
        if (msg.role === "system") continue; // 系统提示重建
        messages.push(msg as Message);
      }
    }

    const repaired = this.repair(messages);
    this.title = title || this.title;

    const session = new Session(systemPrompt());
    for (const msg of repaired) session.append(msg);
    session.refreshSystem(systemPrompt());
    this.attach(session);
    return session;
  }

  /**
   * 修掉残缺的工具调用：assistant 声明了 N 个调用，只有全部结果都回来才算完整。
   * 少一条就整组丢弃 —— 部分配对的 tool 集会被严格的服务端直接拒绝。
   */
  private repair(messages: Message[]): Message[] {
    const out: Message[] = [];
    for (let i = 0; i < messages.length; i += 1) {
      const msg = messages[i] as Message;

      if (msg.role === "assistant" && Array.isArray(msg.tool_calls) && msg.tool_calls.length > 0) {
        const wanted = new Set(msg.tool_calls.map((call) => call.id));
        const following: Message[] = [];
        let j = i + 1;
        while (j < messages.length && (messages[j] as Message).role === "tool") {
          following.push(messages[j] as Message);
          j += 1;
        }
        const answered = new Set(following.map((tool) => tool.tool_call_id));
        const complete = [...wanted].every((id) => answered.has(id));
        if (complete) {
          out.push(msg, ...following);
        }
        i = j - 1; // 跳过已消费的 tool 段（不完整就整段丢掉）
        continue;
      }

      if (msg.role === "tool") continue; // 孤儿 tool：没有父调用，丢掉
      out.push(msg);
    }
    return out;
  }

  /** 列出全部存档，按最近修改倒序 */
  static list(directory: string = SESSIONS_DIR): SessionInfo[] {
    if (!existsSync(directory)) return [];
    const items: SessionInfo[] = [];
    for (const name of readdirSync(directory)) {
      if (!name.endsWith(".jsonl")) continue;
      const file = join(directory, name);
      const events = readLines(file);
      let title = "";
      let turns = 0;
      for (const event of events) {
        if (event.type === "meta") title = event.title || "";
        else if (event.type === "title") title = event.title || title;
        else if (event.type === "msg" && event.role === "user") turns += 1;
      }
      const stat = statSync(file);
      items.push({
        id: name.replace(/\.jsonl$/, ""),
        title: title || "（未命名）",
        turns,
        updated: stat.mtimeMs,
        path: file,
      });
    }
    return items.sort((a, b) => b.updated - a.updated);
  }

  /** 按 id 或 "last" 恢复；找不到抛错 */
  static resume(idOrLast: string, directory: string = SESSIONS_DIR): SessionStore {
    if (idOrLast === "last") {
      const latest = SessionStore.list(directory)[0];
      if (!latest) throw new Error("还没有任何会话存档");
      return new SessionStore(directory, latest.id);
    }
    const file = join(directory, `${idOrLast}.jsonl`);
    if (!existsSync(file)) throw new Error(`找不到会话：${idOrLast}`);
    return new SessionStore(directory, idOrLast);
  }

  /** 删掉一个存档（界面上"删除会话"） */
  static remove(id: string, directory: string = SESSIONS_DIR): void {
    const file = join(directory, `${id}.jsonl`);
    if (existsSync(file)) unlinkSync(file);
  }
}

export { MAX_KEPT };
