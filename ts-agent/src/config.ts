/**
 * 配置与环境。对应 Python 版的 miniagent/config.py。
 *
 * 单一真相源：只有这里读 process.env 和 .env 文件，其他模块都从这取。
 * Key 一律不给默认值 —— 默认无密钥，用户自己配。
 */

import { existsSync, readFileSync } from "node:fs";
import { dirname, join, resolve } from "node:path";
import { fileURLToPath } from "node:url";

const HERE = dirname(fileURLToPath(import.meta.url));
/** 仓库根（src 的上一级） */
export const BASE_DIR = resolve(HERE, "..");
export const AGENT_DIR = join(BASE_DIR, ".agent");

export const APP_NAME = "墟";
export const KEY_ENV_NAMES = ["DEEPSEEK_API_KEY", "OPENAI_API_KEY", "AGENT_API_KEY"] as const;
export const DEFAULT_MODEL = "deepseek-chat";
export const DEFAULT_BASE_URL = "https://api.deepseek.com/v1";
export const MAX_TOOL_OUTPUT = 8000;

export class ConfigError extends Error {
  constructor(message: string) {
    super(message);
    this.name = "ConfigError";
  }
}

/** 把 .env 读进 process.env，不覆盖已有变量 */
export function loadEnv(): void {
  const file = join(BASE_DIR, ".env");
  if (!existsSync(file)) return;
  for (const raw of readFileSync(file, "utf8").split(/\r?\n/)) {
    const line = raw.trim();
    if (!line || line.startsWith("#") || !line.includes("=")) continue;
    const idx = line.indexOf("=");
    const key = line.slice(0, idx).trim();
    if (!key || process.env[key] !== undefined) continue;
    process.env[key] = line.slice(idx + 1).trim().replace(/^["']|["']$/g, "");
  }
}

export interface ApiKeyOptions {
  /** 允许本地模型免 Key（地址是本机/内网时） */
  baseUrl?: string;
}

export function apiKey(explicit?: string | null, _opts: ApiKeyOptions = {}): string {
  if (explicit) return explicit;
  for (const name of KEY_ENV_NAMES) {
    const value = process.env[name];
    if (value && value.trim()) return value.trim();
  }
  throw new ConfigError(
    "没找到 API Key。任选一种：\n" +
      "  1) 设置环境变量 DEEPSEEK_API_KEY\n" +
      "  2) 在 ts-agent/.env 写一行 DEEPSEEK_API_KEY=sk-xxx\n" +
      "  3) 启动时传 --api-key sk-xxx",
  );
}

export function defaultModel(): string {
  return process.env["AGENT_MODEL"] || DEFAULT_MODEL;
}

export function defaultBaseUrl(): string {
  return process.env["AGENT_BASE_URL"] || DEFAULT_BASE_URL;
}

/** 读整数环境变量，坏值回落默认 */
export function envInt(name: string, fallback: number): number {
  const raw = process.env[name];
  if (!raw) return fallback;
  const parsed = Number.parseInt(raw, 10);
  return Number.isFinite(parsed) ? parsed : fallback;
}

/** 读浮点环境变量，坏值回落默认 */
export function envFloat(name: string, fallback: number): number {
  const raw = process.env[name];
  if (!raw) return fallback;
  const parsed = Number.parseFloat(raw);
  return Number.isFinite(parsed) ? parsed : fallback;
}

/** 上下文预算（token），带下限保护 */
export function contextBudget(): number {
  const raw = envInt("AGENT_CONTEXT_TOKENS", 32000);
  return Math.max(4000, raw);
}
