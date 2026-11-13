/**
 * MCP 传输层：按行分隔的 JSON-RPC over stdio。对应 Python 版 mcp/client.ts。
 *
 * 三条实现要点（和 Python 版一样，踩过的坑）：
 *   1. 必须后台读 stdout：server 会随时吐通知，不读会把管道塞满卡死子进程
 *   2. stderr 也要抽干：很多 server 一启动就刷日志
 *   3. 按 id 派发：多线程/多请求共用管道，响应顺序不保证
 */

import { spawn, type ChildProcessWithoutNullStreams } from "node:child_process";
import { createInterface, type Interface } from "node:readline";

export const PROTOCOL_VERSION = "2024-11-05";
export const CLIENT_INFO = { name: "xu-agent", version: "0.1" };

export class MCPError extends Error {
  constructor(message: string) {
    super(message);
    this.name = "MCPError";
  }
}

export class MCPTimeout extends MCPError {
  constructor(message: string) {
    super(message);
    this.name = "MCPTimeout";
  }
}

export interface ServerConfig {
  command: string;
  args?: string[];
  env?: Record<string, string>;
  cwd?: string;
  /** 单次调用超时（秒） */
  timeout?: number;
}

interface Pending {
  resolve: (value: Record<string, unknown>) => void;
  reject: (err: Error) => void;
  timer: NodeJS.Timeout;
}

export class MCPClient {
  readonly name: string;
  private readonly config: ServerConfig;
  private child: ChildProcessWithoutNullStreams | null = null;
  private lines: Interface | null = null;
  private readonly pending = new Map<number, Pending>();
  private nextId = 1;
  private dead = false;
  private lastError = "";

  constructor(name: string, config: ServerConfig) {
    this.name = name;
    this.config = config;
  }

  get alive(): boolean {
    return this.child !== null && !this.dead;
  }

  /** 启动子进程并完成握手 + 工具发现 */
  async start(): Promise<Array<Record<string, unknown>>> {
    if (this.alive) return this.listTools();

    const child = spawn(this.config.command, this.config.args ?? [], {
      cwd: this.config.cwd,
      env: { ...process.env, ...(this.config.env ?? {}) } as Record<string, string>,
      windowsHide: true,
      stdio: ["pipe", "pipe", "pipe"],
    });
    this.child = child;
    this.dead = false;

    child.on("error", (err) => {
      this.lastError = err.message;
      this.fail(new MCPError(`启动失败：${err.message}`));
    });
    child.on("close", (code) => {
      this.dead = true;
      this.fail(new MCPError(`server 退出（code ${code}）${this.lastError ? `：${this.lastError}` : ""}`));
    });

    // stdout：一行一个 JSON-RPC 消息
    this.lines = createInterface({ input: child.stdout });
    this.lines.on("line", (line) => this.onLine(line));
    // stderr：抽干，留最后几行当排错线索
    child.stderr.on("data", (chunk: Buffer) => {
      const text = chunk.toString("utf8").trim();
      if (text) this.lastError = text.split("\n").slice(-3).join(" / ").slice(0, 300);
    });

    await this.request("initialize", {
      protocolVersion: PROTOCOL_VERSION,
      capabilities: {},
      clientInfo: CLIENT_INFO,
    });
    this.notify("notifications/initialized", {});
    return this.listTools();
  }

  private onLine(line: string): void {
    const text = line.trim();
    if (!text) return;
    let payload: Record<string, unknown>;
    try {
      payload = JSON.parse(text) as Record<string, unknown>;
    } catch {
      return; // 非 JSON 行（server 自己打的日志）忽略
    }
    const id = payload["id"];
    if (typeof id !== "number") return; // 通知没有 id
    const slot = this.pending.get(id);
    if (!slot) return;
    this.pending.delete(id);
    clearTimeout(slot.timer);

    const error = payload["error"] as { message?: string } | undefined;
    if (error) slot.reject(new MCPError(error.message ?? "server 返回错误"));
    else slot.resolve((payload["result"] ?? {}) as Record<string, unknown>);
  }

  private write(payload: Record<string, unknown>): void {
    if (!this.child || this.dead) throw new MCPError(`server ${this.name} 未连接`);
    this.child.stdin.write(`${JSON.stringify(payload)}\n`);
  }

  private notify(method: string, params: Record<string, unknown>): void {
    this.write({ jsonrpc: "2.0", method, params });
  }

  request(method: string, params: Record<string, unknown>): Promise<Record<string, unknown>> {
    const timeout = (this.config.timeout ?? 30) * 1000;
    const id = this.nextId;
    this.nextId += 1;

    return new Promise<Record<string, unknown>>((resolve, reject) => {
      const timer = setTimeout(() => {
        this.pending.delete(id);
        reject(new MCPTimeout(`调用 ${method} 超时（${timeout / 1000}s）`));
      }, timeout);
      this.pending.set(id, { resolve, reject, timer });
      try {
        this.write({ jsonrpc: "2.0", id, method, params });
      } catch (err) {
        clearTimeout(timer);
        this.pending.delete(id);
        reject(err as Error);
      }
    });
  }

  async listTools(): Promise<Array<Record<string, unknown>>> {
    const result = await this.request("tools/list", {});
    const tools = result["tools"];
    return Array.isArray(tools) ? (tools as Array<Record<string, unknown>>) : [];
  }

  /** 调工具，返回拼好的文本（MCP 的 content 数组 → 纯文本） */
  async callTool(tool: string, args: Record<string, unknown>): Promise<string> {
    const result = await this.request("tools/call", { name: tool, arguments: args });
    if (result["isError"] === true) {
      const body = flatten(result);
      return `[ERR] ${body}`;
    }
    return flatten(result);
  }

  private fail(err: Error): void {
    for (const [, slot] of this.pending) {
      clearTimeout(slot.timer);
      slot.reject(err);
    }
    this.pending.clear();
  }

  close(): void {
    this.dead = true;
    this.fail(new MCPError("连接已关闭"));
    try {
      this.lines?.close();
    } catch {
      /* 忽略 */
    }
    if (this.child) {
      if (process.platform === "win32" && this.child.pid != null) {
        spawn("taskkill", ["/pid", String(this.child.pid), "/T", "/F"], { windowsHide: true });
      } else {
        this.child.kill("SIGKILL");
      }
      this.child = null;
    }
  }
}

/** MCP 的 content 数组压成纯文本：文本直出，图片只留一句说明（图片进上下文太贵） */
export function flatten(result: Record<string, unknown>): string {
  const content = result["content"];
  if (!Array.isArray(content)) return JSON.stringify(result).slice(0, 2000);
  const parts: string[] = [];
  for (const raw of content) {
    const item = raw as Record<string, unknown>;
    if (item["type"] === "text" && typeof item["text"] === "string") parts.push(item["text"]);
    else if (item["type"] === "image") {
      const mime = String(item["mimeType"] ?? "image/png");
      parts.push(`[image ${mime} 已省略]`);
    } else parts.push(JSON.stringify(item).slice(0, 200));
  }
  return parts.join("\n").trim() || "（无输出）";
}
