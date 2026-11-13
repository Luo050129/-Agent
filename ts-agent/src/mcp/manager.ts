/**
 * MCP 服务器管理。对应 Python 版 mcp/manager.ts。
 *
 * 三件事：
 *   1. 读 .agent/mcp.json，懒连接（第一次调用才启动进程）
 *   2. tools/list 的 schema 转成 OpenAI 工具定义，名字加 mcp__{server}__ 前缀
 *   3. 工具清单压缩：description 截断、参数只留名字 —— 五十个 MCP 工具能少掉几千 token
 *
 * 配置格式（.agent/mcp.json）：
 *   { "servers": { "filesystem": { "command": "npx", "args": ["-y", "@modelcontextprotocol/server-filesystem", "."] } } }
 */

import { existsSync, mkdirSync, readFileSync, writeFileSync } from "node:fs";
import { dirname, join } from "node:path";
import { AGENT_DIR } from "../config.ts";
import { MCPClient, MCPError, type ServerConfig } from "./client.ts";

export const MCP_CONFIG = join(AGENT_DIR, "mcp.json");
const NAME_PREFIX = "mcp__";
const DESC_MAX = 90;

interface ToolsCache {
  [server: string]: Array<Record<string, unknown>>;
}

export interface ServerStatus {
  name: string;
  tools: number;
  alive: boolean;
  error: string;
}

export class MCPManager {
  private readonly clients = new Map<string, MCPClient>();
  private readonly tools = new Map<string, Array<Record<string, unknown>>>(); // server → 原始工具
  private readonly cacheFile: string;
  private configPath: string;
  private schemas: Array<Record<string, unknown>> = [];
  private errors = new Map<string, string>();

  constructor(configPath: string = MCP_CONFIG, cacheFile: string = join(AGENT_DIR, "mcp-tools.json")) {
    this.configPath = configPath;
    this.cacheFile = cacheFile;
  }

  servers(): Record<string, ServerConfig> {
    if (!existsSync(this.configPath)) return {};
    try {
      const raw = JSON.parse(readFileSync(this.configPath, "utf8")) as { servers?: Record<string, ServerConfig> };
      return raw.servers ?? {};
    } catch {
      return {};
    }
  }

  /** 连上所有配置的 server，返回 OpenAI 工具定义（失败的 server 跳过） */
  async load(): Promise<Array<Record<string, unknown>>> {
    const schemas: Array<Record<string, unknown>> = [];
    for (const [name, config] of Object.entries(this.servers())) {
      try {
        const tools = await this.ensure(name, config);
        for (const tool of tools) schemas.push(this.toSchema(name, tool));
      } catch (err) {
        this.errors.set(name, (err as Error).message);
      }
    }
    this.schemas = schemas;
    this.saveCache();
    return schemas;
  }

  private async ensure(name: string, config: ServerConfig): Promise<Array<Record<string, unknown>>> {
    const cached = this.tools.get(name);
    if (cached) return cached;

    let client = this.clients.get(name);
    if (!client) {
      client = new MCPClient(name, config);
      this.clients.set(name, client);
    }
    const tools = await client.start();
    this.tools.set(name, tools);
    this.errors.delete(name);
    return tools;
  }

  /** 这里就是压缩：长描述截断、参数只留名字和必填，不塞 JSON Schema 全文 */
  private toSchema(server: string, raw: Record<string, unknown>): Record<string, unknown> {
    const name = String(raw["name"] ?? "");
    const description = String(raw["description"] ?? "").split("\n")[0] ?? "";
    const input = (raw["inputSchema"] ?? {}) as Record<string, unknown>;
    const properties = (input["properties"] ?? {}) as Record<string, Record<string, unknown>>;
    const required = new Set(Array.isArray(input["required"]) ? (input["required"] as string[]) : []);

    const params: Record<string, unknown> = {};
    for (const [key, spec] of Object.entries(properties)) {
      const type = String(spec["type"] ?? "string");
      const hint = String(spec["description"] ?? "").split("\n")[0]?.slice(0, 40) ?? "";
      params[key] = {
        type,
        ...(required.has(key) ? {} : {}),
        description: hint || `${required.has(key) ? "必填" : "可选"}参数`,
      };
    }

    return {
      type: "function",
      function: {
        name: `${NAME_PREFIX}${server}__${name}`,
        description: description.length > DESC_MAX ? `${description.slice(0, DESC_MAX)}…` : description,
        parameters: {
          type: "object",
          properties: params,
          required: [...required],
        },
      },
    };
  }

  /** 是否是 MCP 工具名 */
  isMcpTool(name: string): boolean {
    return name.startsWith(NAME_PREFIX);
  }

  /** 调 MCP 工具：拆 server__tool，懒连接 */
  async call(fullName: string, args: Record<string, unknown>): Promise<string> {
    const rest = fullName.slice(NAME_PREFIX.length);
    const sep = rest.indexOf("__");
    if (sep === -1) return `[ERR] MCP 工具名格式不对：${fullName}`;
    const server = rest.slice(0, sep);
    const tool = rest.slice(sep + 2);

    const config = this.servers()[server];
    if (!config) return `[ERR] 未配置的 MCP server：${server}`;

    try {
      const client = this.clients.get(server) ?? new MCPClient(server, config);
      this.clients.set(server, client);
      if (!client.alive) await client.start();
      return await client.callTool(tool, args);
    } catch (err) {
      return `[ERR] MCP 调用失败（${server}/${tool}）：${(err as Error).message}`;
    }
  }

  status(): ServerStatus[] {
    return Object.keys(this.servers()).map((name) => ({
      name,
      tools: this.tools.get(name)?.length ?? 0,
      alive: this.clients.get(name)?.alive ?? false,
      error: this.errors.get(name) ?? "",
    }));
  }

  /** 注入工具清单：给系统提示用的一行摘要 */
  usage(): { servers: number; injected: number; note: string } {
    const servers = Object.keys(this.servers()).length;
    const injected = this.schemas.length;
    const note = injected === 0 ? "还没接 MCP 服务器" : `已注入 ${injected} 个 MCP 工具`;
    return { servers, injected, note };
  }

  private saveCache(): void {
    try {
      mkdirSync(dirname(this.cacheFile), { recursive: true });
      writeFileSync(this.cacheFile, JSON.stringify(this.schemas, null, 2), "utf8");
    } catch {
      /* 缓存失败不影响使用 */
    }
  }

  closeAll(): void {
    for (const [, client] of this.clients) client.close();
    this.clients.clear();
  }
}

export const mcpManager = new MCPManager();
export { MCPError };
