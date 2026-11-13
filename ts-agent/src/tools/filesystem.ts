/**
 * 读写文件与列目录。对应 Python 版的 tools/filesystem.ts。
 *
 * TS 版先不带沙箱与检查点（Python 版的 tools/sandbox.ts、checkpoints.ts 是下一步），
 * 但边界检查（读/写白名单）从第一版就在 —— 那是安全底线，不能后补。
 */

import { existsSync, mkdirSync, readdirSync, readFileSync, statSync, writeFileSync } from "node:fs";
import { dirname, isAbsolute, join, resolve } from "node:path";
import { BASE_DIR } from "../config.ts";
import { ERR } from "../markers.ts";
import * as checkpoints from "./checkpoints.ts";
import { truncate } from "./util.ts";

/** 允许读/写的根目录，可用环境变量追加（分号分隔） */
function roots(kind: "read" | "write"): string[] {
  const envName = kind === "read" ? "AGENT_READ_ROOTS" : "AGENT_WRITE_ROOTS";
  const extra = (process.env[envName] ?? "")
    .split(";")
    .map((p) => p.trim())
    .filter(Boolean);
  const base = kind === "read" ? [BASE_DIR, process.cwd()] : [BASE_DIR];
  return [...base, ...extra].map((p) => resolve(p));
}

/** Windows 盘符 / UNC 路径：非 Windows 平台 resolve 会把它当相对路径，必须特殊对待 */
const DRIVE_PATH = /^[A-Za-z]:[\\/]|^\\\\|^\/\//;

function inside(path: string, allowed: string[]): boolean {
  // 非 Windows 没有盘符语义：C:/Windows/x 会被解析进 cwd，恰好落进白名单
  if (process.platform !== "win32" && DRIVE_PATH.test(path)) return false;
  const target = resolve(path);
  return allowed.some((root) => target === root || target.startsWith(root + (process.platform === "win32" ? "\\" : "/")));
}

/** 相对路径按 cwd 解析，绝对路径原样 */
function toPath(raw: unknown): string {
  const text = String(raw ?? "").trim();
  if (!text) throw new Error("缺少 path 参数");
  return isAbsolute(text) ? text : resolve(process.cwd(), text);
}

function readFile(args: Record<string, unknown>): string {
  const path = toPath(args["path"]);
  if (!inside(path, roots("read"))) {
    return `${ERR}读取范围之外，只允许读：${roots("read").join("、")}`;
  }
  if (!existsSync(path) || !statSync(path).isFile()) {
    return `${ERR}文件不存在或不是文件：${path}`;
  }
  const start = Math.max(1, Number(args["start"] ?? 1));
  const limit = Math.max(1, Number(args["limit"] ?? 300));
  const lines = readFileSync(path, "utf8").split(/\r?\n/);
  const chunk = lines.slice(start - 1, start - 1 + limit);
  if (chunk.length === 0) {
    return `${ERR}文件共 ${lines.length} 行，指定区间为空。`;
  }
  const body = chunk.map((line, i) => `${String(start + i).padStart(5)}| ${line}`).join("\n");
  const tail = start - 1 + limit >= lines.length ? "" : `\n...（共 ${lines.length} 行）`;
  return truncate(body + tail);
}

function writeFile(args: Record<string, unknown>): string {
  const path = toPath(args["path"]);
  if (!inside(path, roots("write"))) {
    return `${ERR}写入范围之外，只允许写：${roots("write").join("、")}`;
  }
  const content = String(args["content"] ?? "");
  const snap = checkpoints.snapshot(path); // 先快照；快照失败不挡写入，只是这次不能撤销
  try {
    mkdirSync(dirname(path), { recursive: true });
    writeFileSync(path, content, "utf8");
  } catch (err) {
    return `${ERR}写入失败：${(err as Error).message}`;
  }
  checkpoints.save(snap); // 写入成功才落盘，避免"撤销一个没发生的写入"
  return `已写入 ${path}（${content.length} 字符）`;
}

function listDir(args: Record<string, unknown>): string {
  const path = toPath(args["path"] ?? ".");
  if (!inside(path, roots("read"))) {
    return `${ERR}读取范围之外，只允许读：${roots("read").join("、")}`;
  }
  if (!existsSync(path) || !statSync(path).isDirectory()) {
    return `${ERR}目录不存在：${path}`;
  }
  const entries = readdirSync(path, { withFileTypes: true })
    .sort((a, b) => Number(b.isDirectory()) - Number(a.isDirectory()) || a.name.localeCompare(b.name))
    .map((item) => {
      if (item.isDirectory()) return `[DIR]  ${item.name}/`;
      const size = statSync(join(path, item.name)).size;
      return `[FILE] ${item.name}  (${size} B)`;
    });
  return truncate(`目录 ${path} 共 ${entries.length} 项：\n${entries.join("\n")}`);
}

export const schemas: Array<Record<string, unknown>> = [
  {
    type: "function",
    function: {
      name: "read_file",
      description: "按行读取文本文件内容（带行号）。需要看代码、日志、配置时使用。",
      parameters: {
        type: "object",
        properties: {
          path: { type: "string", description: "文件路径" },
          start: { type: "integer", description: "起始行号，从 1 开始" },
          limit: { type: "integer", description: "最多读多少行，默认 300" },
        },
        required: ["path"],
      },
    },
  },
  {
    type: "function",
    function: {
      name: "write_file",
      description: "写入（覆盖）文本文件，父目录不存在会自动创建。",
      parameters: {
        type: "object",
        properties: {
          path: { type: "string", description: "文件路径" },
          content: { type: "string", description: "完整文件内容" },
        },
        required: ["path", "content"],
      },
    },
  },
  {
    type: "function",
    function: {
      name: "list_dir",
      description: "列出目录内容，区分目录和文件并给出大小。不确定文件在哪时先列目录。",
      parameters: {
        type: "object",
        properties: { path: { type: "string", description: "目录路径，默认当前目录" } },
      },
    },
  },
];

export const handlers = {
  read_file: readFile,
  write_file: writeFile,
  list_dir: listDir,
};

export const dangerous = ["write_file"];
