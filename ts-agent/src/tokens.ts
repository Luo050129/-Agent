/**
 * token 估算与截断。对应 Python 版的 miniagent/tokens.py。
 *
 * 全项目唯一的长度单位。中文 1 字 ≈ 1 token，英文 4 字符 ≈ 1 token。
 * 截断、预算、压缩都走这里，禁止各处自己算 length —— 中文按字符算会差四倍。
 */

/** 估算一段文本的 token 数：CJK 按字、其余按 4 字符 1 token */
export function estimateText(text: string): number {
  if (!text) return 0;
  let cjk = 0;
  let other = 0;
  for (const ch of text) {
    const code = ch.codePointAt(0) ?? 0;
    // CJK 统一表意文字、扩展 A、全角符号、日文假名、韩文
    if (
      (code >= 0x4e00 && code <= 0x9fff) ||
      (code >= 0x3400 && code <= 0x4dbf) ||
      (code >= 0x3000 && code <= 0x303f) ||
      (code >= 0x3040 && code <= 0x30ff) ||
      (code >= 0xac00 && code <= 0xd7af) ||
      (code >= 0xff00 && code <= 0xffef)
    ) {
      cjk += 1;
    } else {
      other += 1;
    }
  }
  return cjk + Math.ceil(other / 4);
}

/** 按 token 预算二分找前缀边界 */
export function takePrefix(text: string, maxTokens: number): string {
  if (maxTokens <= 0) return "";
  if (estimateText(text) <= maxTokens) return text;
  let lo = 0;
  let hi = text.length;
  while (lo < hi) {
    const mid = Math.ceil((lo + hi) / 2);
    if (estimateText(text.slice(0, mid)) <= maxTokens) lo = mid;
    else hi = mid - 1;
  }
  return text.slice(0, lo);
}

/** 按 token 预算二分找后缀边界 */
export function takeSuffix(text: string, maxTokens: number): string {
  if (maxTokens <= 0) return "";
  if (estimateText(text) <= maxTokens) return text;
  let lo = 0;
  let hi = text.length;
  while (lo < hi) {
    const mid = Math.ceil((lo + hi) / 2);
    if (estimateText(text.slice(text.length - mid)) <= maxTokens) lo = mid;
    else hi = mid - 1;
  }
  return text.slice(text.length - lo);
}

export interface ClipOptions {
  /** 是否在句号/换行处收尾，默认 true */
  sentence?: boolean;
  ellipsis?: string;
}

/** 通用截断：能收在句末就收在句末 */
export function clip(text: string, maxTokens: number, opts: ClipOptions = {}): string {
  const { sentence = true, ellipsis = "…" } = opts;
  if (estimateText(text) <= maxTokens) return text;
  let out = takePrefix(text, Math.max(0, maxTokens - estimateText(ellipsis)));
  if (sentence && out) {
    const lastStop = Math.max(
      out.lastIndexOf("。"),
      out.lastIndexOf("\n"),
      out.lastIndexOf("！"),
      out.lastIndexOf("？"),
      out.lastIndexOf(". "),
    );
    if (lastStop > out.length * 0.6) out = out.slice(0, lastStop + 1);
  }
  return out.trimEnd() + ellipsis;
}

/** 估算一整组消息的 token（每条加固定开销） */
export function estimateMessages(messages: Array<Record<string, unknown>>): number {
  let total = 3;
  for (const msg of messages) {
    total += 4;
    const content = msg["content"];
    if (typeof content === "string") total += estimateText(content);
    else if (content != null) total += estimateText(JSON.stringify(content));
    const calls = msg["tool_calls"];
    if (Array.isArray(calls)) total += estimateText(JSON.stringify(calls));
  }
  return total;
}
