/**
 * 插件：一个目录一个插件，入口 `plugin.mjs` 暴露 SCHEMAS 和 HANDLERS。
 * 对应 Python 版 plugins/manager.ts。
 *
 * 插件是第三方代码，装进来等于允许它在你的机器上跑 —— 所以这里不做任何
 * 自动执行，只在明确调用工具时才 import，且加载失败只影响它自己。
 */

import { existsSync, mkdirSync, readdirSync, readFileSync, statSync, writeFileSync } from "node:fs";
import { basename, join } from "node:path";
import { pathToFileURL } from "node:url";
import { BASE_DIR } from "./config.ts";
import { readZip } from "./zip.ts";

export const PLUGINS_DIR = join(BASE_DIR, "plugins");
const ENTRY = "plugin.mjs";
const META = "meta.json";

export class PluginError extends Error {
  constructor(message: string) {
    super(message);
    this.name = "PluginError";
  }
}

export interface PluginInfo {
  name: string;
  path: string;
  description: string;
  tools: string[];
  error: string;
}

interface LoadedModule {
  SCHEMAS?: Array<Record<string, unknown>>;
  HANDLERS?: Record<string, (args: Record<string, unknown>) => string | Promise<string>>;
  META?: { name?: string; description?: string; version?: string };
}

const cache = new Map<string, LoadedModule>();

async function importPlugin(dir: string, name: string): Promise<LoadedModule> {
  const cached = cache.get(name);
  if (cached) return cached;

  const file = join(dir, ENTRY);
  if (!existsSync(file)) throw new PluginError(`缺少 ${ENTRY}`);

  try {
    const mod = (await import(`${pathToFileURL(file).href}?t=${Date.now()}`)) as LoadedModule;
    if (!mod.HANDLERS || typeof mod.HANDLERS !== "object") {
      throw new PluginError(`${ENTRY} 里没有导出 HANDLERS`);
    }
    cache.set(name, mod);
    return mod;
  } catch (err) {
    if (err instanceof PluginError) throw err;
    throw new PluginError(`导入失败：${(err as Error).name}: ${(err as Error).message}`);
  }
}

export function list(): PluginInfo[] {
  if (!existsSync(PLUGINS_DIR)) return [];
  const out: PluginInfo[] = [];
  for (const name of readdirSync(PLUGINS_DIR).sort()) {
    const dir = join(PLUGINS_DIR, name);
    if (!statSync(dir).isDirectory() || name.startsWith("_") || name.startsWith(".")) continue;

    let description = "";
    const metaFile = join(dir, META);
    if (existsSync(metaFile)) {
      try {
        description = String((JSON.parse(readFileSync(metaFile, "utf8")) as { description?: string }).description ?? "");
      } catch {
        /* meta 坏了不影响加载 */
      }
    }

    const info: PluginInfo = { name, path: dir, description, tools: [], error: "" };
    if (!existsSync(join(dir, ENTRY))) {
      info.error = `缺少 ${ENTRY}`;
    } else {
      try {
        const mod = cache.get(name);
        info.tools = mod ? Object.keys(mod.HANDLERS ?? {}) : [ENTRY];
      } catch {
        /* 列表阶段不 import，留给 loadTools */
      }
    }
    out.push(info);
  }
  return out;
}

/** 加载所有插件工具（坏的跳过，不拖垮启动） */
export async function loadTools(): Promise<{
  schemas: Array<Record<string, unknown>>;
  handlers: Record<string, (args: Record<string, unknown>) => string | Promise<string>>;
  loaded: string[];
  failed: Array<{ name: string; error: string }>;
}> {
  const schemas: Array<Record<string, unknown>> = [];
  const handlers: Record<string, (args: Record<string, unknown>) => string | Promise<string>> = {};
  const loaded: string[] = [];
  const failed: Array<{ name: string; error: string }> = [];

  for (const info of list()) {
    if (info.error) {
      failed.push({ name: info.name, error: info.error });
      continue;
    }
    try {
      const mod = await importPlugin(info.path, info.name);
      for (const schema of mod.SCHEMAS ?? []) {
        const name = (schema["function"] as { name?: string } | undefined)?.name;
        if (name && !handlers[name]) schemas.push(schema);
      }
      for (const [name, fn] of Object.entries(mod.HANDLERS ?? {})) {
        if (typeof fn === "function" && !handlers[name]) handlers[name] = fn;
      }
      loaded.push(info.name);
    } catch (err) {
      failed.push({ name: info.name, error: (err as Error).message });
    }
  }
  return { schemas, handlers, loaded, failed };
}

/** 装插件：本地目录或 GitHub（取 zip 里的 plugin.mjs 所在目录） */
export async function install(source: string, name?: string): Promise<PluginInfo> {
  const src = (source ?? "").trim();
  if (!src) throw new PluginError("地址不能为空");

  let destName = name ?? "";
  let files: Map<string, Buffer> = new Map();

  if (existsSync(src) && statSync(src).isDirectory()) {
    if (!existsSync(join(src, ENTRY))) throw new PluginError(`目录里没有 ${ENTRY}`);
    destName = destName || basename(src);
    for (const file of readdirSync(src)) {
      const full = join(src, file);
      if (statSync(full).isFile()) files.set(file, readFileSync(full));
    }
  } else if (src.includes("github.com")) {
    const parts = src.replace(/\/+$/, "").split("github.com/")[1]?.split("/") ?? [];
    if (parts.length < 2) throw new PluginError("GitHub 地址格式不对");
    const repo = `${parts[0]}/${parts[1]}`;
    const refs = [parts[3], "main", "master"].filter(Boolean) as string[];

    let payload: Buffer | null = null;
    for (const ref of refs) {
      const resp = await fetch(`https://codeload.github.com/${repo}/zip/refs/heads/${ref}`);
      if (resp.ok) {
        payload = Buffer.from(await resp.arrayBuffer());
        break;
      }
    }
    if (!payload) throw new PluginError(`下载仓库失败：${repo}`);

    const all = readZip(payload);
    const entryPath = [...all.keys()].find((p) => p.endsWith(`/${ENTRY}`) || p === ENTRY);
    if (!entryPath) throw new PluginError(`仓库里没有 ${ENTRY}`);
    const prefix = entryPath.slice(0, entryPath.length - ENTRY.length);
    destName = destName || basename(prefix.replace(/\/+$/, ""));
    for (const [path, buf] of all.entries()) {
      if (prefix && !path.startsWith(prefix)) continue;
      files.set(path.slice(prefix.length), buf);
    }
  } else {
    throw new PluginError("只支持本地目录或 GitHub 地址");
  }

  if (!files.has(ENTRY)) throw new PluginError(`缺少 ${ENTRY}`);
  const dir = join(PLUGINS_DIR, destName);
  mkdirSync(dir, { recursive: true });
  for (const [rel, buf] of files.entries()) {
    if (!rel || rel.endsWith("/")) continue;
    const target = join(dir, rel);
    mkdirSync(join(target, ".."), { recursive: true });
    writeFileSync(target, buf);
  }

  cache.delete(destName);
  const mod = await importPlugin(dir, destName);
  writeFileSync(
    join(dir, META),
    JSON.stringify({ name: destName, description: String(mod.META?.description ?? ""), source: src }, null, 2),
    "utf8",
  );
  return {
    name: destName,
    path: dir,
    description: String(mod.META?.description ?? ""),
    tools: Object.keys(mod.HANDLERS ?? {}),
    error: "",
  };
}
