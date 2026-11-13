/**
 * 工具层公用小工具。对应 Python 版的 tools/_util.ts。
 *
 * 单独成文件是为了避免循环依赖：index 汇总工具，工具又要用截断和标记。
 */

import { MAX_TOOL_OUTPUT } from "../config.ts";
import { ERR_TAG, OK, OK_TAG } from "../markers.ts";

/** 成功结果里出现这些词，会被关键词兜底误判成失败，需要主动豁免 */
const AMBIGUOUS = ["失败", "不存在", "超时", "超出", "不支持", "无法还原", "解析失败", "被拒绝"];

export function truncate(text: string, limit = MAX_TOOL_OUTPUT): string {
  if (text.length <= limit) return text;
  return `${text.slice(0, limit)}\n...（已截断，原长 ${text.length} 字符）`;
}

/** 给没有前缀的结果补标记：成功但含歧义词时加 OK，免得熔断把正常内容当失败 */
export function tagResult(text: string): string {
  if (!text) return text;
  const head = text.slice(0, 40).trimStart();
  if (head.startsWith(ERR_TAG) || head.startsWith(OK_TAG)) return text;
  if (AMBIGUOUS.some((word) => text.slice(0, 200).includes(word))) return OK + text;
  return text;
}
