/**
 * 会话检索。对应 Python 版 core/search.ts。
 *
 * 不建索引，直接扫 JSONL —— 几百个会话、几万行文本，扫一遍是毫秒级，
 * 建索引的复杂度完全不值。字面匹配 + 简单打分，宁可多召回也别漏。
 */

import { existsSync, readFileSync, readdirSync, statSync } from "node:fs";
import { join } from "node:path";
import { SESSIONS_DIR } from "./persistence.ts";

export interface SearchHit {
  sessionId: string;
  title: string;
  role: string;
  snippet: string;
  score: number;
  updated: number;
}

export interface SearchOptions {
  limit?: number;
  dir?: string;
  /** 只搜这些角色，默认全部 */
  roles?: string[];
}

interface StoredLine {
  type?: string;
  role?: string;
  content?: unknown;
  title?: string;
}

/** 关键词切分：空格分隔，忽略空串与单字符外的符号 */
function keywords(query: string): string[] {
  return query
    .toLowerCase()
    .split(/[\s,，、]+/)
    .map((k) => k.trim())
    .filter((k) => k.length > 0);
}

/** 取命中位置附近的一段，避免把整条大消息塞进结果 */
function snippet(text: string, terms: string[], width = 80): string {
  const flat = text.replace(/\s+/g, " ").trim();
  const lower = flat.toLowerCase();
  let at = -1;
  for (const term of terms) {
    const idx = lower.indexOf(term);
    if (idx !== -1 && (at === -1 || idx < at)) at = idx;
  }
  if (at === -1) return flat.slice(0, width);
  const start = Math.max(0, at - Math.floor(width / 3));
  const cut = flat.slice(start, start + width);
  return `${start > 0 ? "…" : ""}${cut}${start + width < flat.length ? "…" : ""}`;
}

/** 单条消息打分：命中词数为主，标题命中额外加权 */
function score(text: string, terms: string[]): number {
  const lower = text.toLowerCase();
  let hits = 0;
  for (const term of terms) {
    let from = 0;
    for (;;) {
      const idx = lower.indexOf(term, from);
      if (idx === -1) break;
      hits += 1;
      from = idx + term.length;
      if (hits > 20) break; // 单条消息别无限累加，长文档会垄断结果
    }
  }
  return hits;
}

/** 跨会话搜索：扫 .agent/sessions/*.jsonl */
export function searchSessions(query: string, options: SearchOptions = {}): SearchHit[] {
  const terms = keywords(query);
  if (terms.length === 0) return [];

  const dir = options.dir ?? SESSIONS_DIR;
  const limit = options.limit ?? 20;
  const roles = options.roles;

  if (!existsSync(dir)) return [];

  const hits: SearchHit[] = [];
  for (const name of readdirSync(dir)) {
    if (!name.endsWith(".jsonl")) continue;
    const file = join(dir, name);
    let updated = 0;
    try {
      updated = statSync(file).mtimeMs;
    } catch {
      continue;
    }

    let title = "";
    let lines: string[];
    try {
      lines = readFileSync(file, "utf8").split(/\r?\n/);
    } catch {
      continue;
    }

    for (const raw of lines) {
      if (!raw.trim()) continue;
      let event: StoredLine;
      try {
        event = JSON.parse(raw) as StoredLine;
      } catch {
        continue;
      }
      if (event.type === "meta" || event.type === "title") {
        if (event.title) title = event.title;
        continue;
      }
      if (event.type !== "msg") continue;
      const role = String(event.role ?? "");
      if (roles && !roles.includes(role)) continue;

      const text = typeof event.content === "string" ? event.content : "";
      if (!text) continue;

      let value = score(text, terms);
      if (value === 0) continue;
      value += score(title, terms) * 2; // 标题命中更值钱
      if (role === "user") value += 1; // 用户原话通常是最准的线索
      value += Math.min(3, updated / 1e13); // 同分时新的略靠前

      hits.push({
        sessionId: name.replace(/\.jsonl$/, ""),
        title: title || "（未命名）",
        role,
        snippet: snippet(text, terms),
        score: value,
        updated,
      });
    }
  }

  return hits.sort((a, b) => b.score - a.score || b.updated - a.updated).slice(0, limit);
}

/** 当前会话内的搜索（不走磁盘） */
export function searchMessages(
  messages: Array<Record<string, unknown>>,
  query: string,
  limit = 10,
): SearchHit[] {
  const terms = keywords(query);
  if (terms.length === 0) return [];

  const hits: SearchHit[] = [];
  messages.forEach((msg, index) => {
    const text = typeof msg["content"] === "string" ? msg["content"] : "";
    if (!text) return;
    const value = score(text, terms);
    if (value === 0) return;
    hits.push({
      sessionId: "current",
      title: `第 ${index + 1} 条`,
      role: String(msg["role"] ?? ""),
      snippet: snippet(text, terms),
      score: value,
      updated: 0,
    });
  });
  return hits.sort((a, b) => b.score - a.score).slice(0, limit);
}

/** 一条命中压成一行给界面看 */
export function formatHit(hit: SearchHit): string {
  const role = hit.role === "user" ? "你" : hit.role === "assistant" ? "助手" : hit.role;
  return `${hit.sessionId.slice(0, 17)}  ${role}  ${hit.snippet}`;
}
