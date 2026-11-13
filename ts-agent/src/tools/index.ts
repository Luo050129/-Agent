/**
 * 工具层出口：内置工具汇总成一张注册表。对应 Python 版的 tools/__init__.py。
 *
 * 三条约定：
 *   1. execute 永不抛异常，任何失败都转成 [ERR] 文本回灌
 *   2. 参数是 JSON 字符串，解析失败也算失败
 *   3. 输出统一走 truncate 截断
 */

import { mcpManager } from "../mcp/manager.ts";
import { ERR } from "../markers.ts";
import * as filesystem from "./filesystem.ts";
import * as runtime from "./runtime.ts";
import * as documents from "./documents.ts";
import * as skills from "./skills.ts";
import { tagResult } from "./util.ts";

export { tagResult, truncate } from "./util.ts";

export interface ToolModule {
  schemas: Array<Record<string, unknown>>;
  handlers: Record<string, (args: Record<string, unknown>) => string | Promise<string>>;
  dangerous: string[];
}

const MODULES: ToolModule[] = [runtime, filesystem, documents, skills];

export const BUILTIN_SCHEMAS: Array<Record<string, unknown>> = MODULES.flatMap((m) => m.schemas);
export const DANGEROUS = new Set(MODULES.flatMap((m) => m.dangerous));

/** MCP 工具是运行时加载的，单独放一层，注册表原地刷新 */
const EXTRA_SCHEMAS: Array<Record<string, unknown>> = [];

export function toolSchemas(): Array<Record<string, unknown>> {
  return [...BUILTIN_SCHEMAS, ...EXTRA_SCHEMAS];
}

/** 插件工具：也是运行时加载，schema 与 handler 一起换 */
const EXTRA_HANDLERS: Record<string, (args: Record<string, unknown>) => string | Promise<string>> = {};

export function setExtraTools(
  schemas: Array<Record<string, unknown>>,
  handlers: Record<string, (args: Record<string, unknown>) => string | Promise<string>>,
): number {
  Object.assign(EXTRA_HANDLERS, handlers);
  for (const name of Object.keys(handlers)) DANGEROUS.add(name);
  return setExtraSchemas(schemas);
}

/** 装载 MCP 工具：schema 进注册表，名字进危险集合（第三方能力，安全模式下要确认） */
export function setExtraSchemas(list: Array<Record<string, unknown>>): number {
  EXTRA_SCHEMAS.length = 0;
  EXTRA_SCHEMAS.push(...list);
  for (const schema of list) {
    const name = (schema["function"] as { name?: string } | undefined)?.name;
    if (name) DANGEROUS.add(name);
  }
  return EXTRA_SCHEMAS.length;
}

export { TOOL_SCHEMAS } from "./schemas.ts";

const HANDLERS: Record<string, (args: Record<string, unknown>) => string | Promise<string>> = {};
for (const mod of MODULES) {
  for (const [name, fn] of Object.entries(mod.handlers)) HANDLERS[name] = fn;
}

/** 按名字 + JSON 参数执行工具，永不抛 */
export async function execute(name: string, rawArgs: string): Promise<string> {
  const fn = HANDLERS[name] ?? EXTRA_HANDLERS[name];
  const isMcp = mcpManager.isMcpTool(name);
  if (!fn && !isMcp) {
    return `${ERR}未知工具：${name}。可用工具：${[...Object.keys(HANDLERS), ...EXTRA_SCHEMAS.map((s) => (s["function"] as { name?: string })?.name)].filter(Boolean).join(", ")}`;
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
    if (isMcp) return tagResult(await mcpManager.call(name, args));
    return tagResult(await (fn as (a: Record<string, unknown>) => string | Promise<string>)(args));
  } catch (err) {
    return `${ERR}工具执行异常：${(err as Error).name}: ${(err as Error).message}`;
  }
}

export function toolNames(): string[] {
  return [...Object.keys(HANDLERS), ...Object.keys(EXTRA_HANDLERS)];
}
