/**
 * 工具层出口：内置工具汇总成一张注册表。对应 Python 版的 tools/__init__.py。
 *
 * 三条约定：
 *   1. execute 永不抛异常，任何失败都转成 [ERR] 文本回灌
 *   2. 参数是 JSON 字符串，解析失败也算失败
 *   3. 输出统一走 truncate 截断
 */

import { ERR } from "../markers.ts";
import * as filesystem from "./filesystem.ts";
import * as runtime from "./runtime.ts";
import { tagResult } from "./util.ts";

export { tagResult, truncate } from "./util.ts";

export interface ToolModule {
  schemas: Array<Record<string, unknown>>;
  handlers: Record<string, (args: Record<string, unknown>) => string | Promise<string>>;
  dangerous: string[];
}

const MODULES: ToolModule[] = [runtime, filesystem];

export const TOOL_SCHEMAS: Array<Record<string, unknown>> = MODULES.flatMap((m) => m.schemas);
export const DANGEROUS = new Set(MODULES.flatMap((m) => m.dangerous));

const HANDLERS: Record<string, (args: Record<string, unknown>) => string | Promise<string>> = {};
for (const mod of MODULES) {
  for (const [name, fn] of Object.entries(mod.handlers)) HANDLERS[name] = fn;
}

/** 按名字 + JSON 参数执行工具，永不抛 */
export async function execute(name: string, rawArgs: string): Promise<string> {
  const fn = HANDLERS[name];
  if (!fn) {
    return `${ERR}未知工具：${name}。可用工具：${Object.keys(HANDLERS).join(", ")}`;
  }

  let args: Record<string, unknown>;
  try {
    args = rawArgs.trim() ? (JSON.parse(rawArgs) as Record<string, unknown>) : {};
    if (typeof args !== "object" || args === null || Array.isArray(args)) {
      return `${ERR}参数必须是 JSON 对象，收到：${rawArgs.slice(0, 200)}`;
    }
  } catch (err) {
    return `${ERR}参数 JSON 解析失败（${(err as Error).message}）：${rawArgs.slice(0, 200)}`;
  }

  try {
    return tagResult(await fn(args));
  } catch (err) {
    return `${ERR}工具执行异常：${(err as Error).name}: ${(err as Error).message}`;
  }
}

export function toolNames(): string[] {
  return Object.keys(HANDLERS);
}
