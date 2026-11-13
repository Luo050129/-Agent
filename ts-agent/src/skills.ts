/**
 * 技能：`skills/*.md` 是按需加载的方法文档。对应 Python 版 skills/manager.ts。
 *
 * 清单（名字 + 一句描述）进系统提示，正文要模型主动调 use_skill 才加载 ——
 * 一堆方法论常驻上下文纯属烧钱。
 *
 * 安装支持本地文件/目录和 GitHub 仓库（走 zip，零依赖自己解）。
 */

import { existsSync, mkdirSync, readdirSync, readFileSync, statSync, writeFileSync } from "node:fs";
import { basename, join, resolve } from "node:path";
import { BASE_DIR } from "./config.ts";
import { readZip } from "./zip.ts";

export const SKILLS_DIR = join(BASE_DIR, "skills");

/** 技能根目录：XU_SKILLS_DIR 可覆盖（测试与多套技能集用） */
export function skillsDir(): string {
  const custom = process.env["XU_SKILLS_DIR"];
  return custom ? resolve(custom) : SKILLS_DIR;
}
const DESC_MAX = 60;
const BODY_MAX = 8000;

export interface Skill {
  key: string;
  name: string;
  description: string;
  path: string;
}

const UNSAFE = /[\\/:*?"<>|]+/g;

function safeName(name: string): string {
  const cleaned = name.replace(UNSAFE, "_").replace(/^[._]+|[._]+$/g, "").slice(0, 80);
  if (!cleaned) throw new Error("技能名不合法");
  return cleaned;
}

function parse(file: string): Skill {
  const text = readFileSync(file, "utf8");
  let name = basename(file, ".md");
  let description = "";
  for (const raw of text.split(/\r?\n/)) {
    const line = raw.trim();
    if (line.startsWith("# ")) {
      name = line.slice(2).trim();
      continue;
    }
    if (line.startsWith(">")) {
      description = line.replace(/^>\s*/, "").trim();
      break;
    }
    if (line && !line.startsWith("#")) {
      description = line;
      break;
    }
  }
  if (description.length > DESC_MAX) description = `${description.slice(0, DESC_MAX)}…`;
  return { key: basename(file, ".md"), name, description: description || "（无描述）", path: file };
}

/** 扫描技能目录（AGENT_SKILLS_DIR 可追加，分号分隔） */
export function skillDirs(): string[] {
  const extra = (process.env["AGENT_SKILLS_DIR"] ?? "")
    .split(";")
    .map((p) => p.trim())
    .filter(Boolean);
  return [skillsDir(), ...extra].filter((dir) => existsSync(dir) && statSync(dir).isDirectory());
}

export function discover(): Skill[] {
  const seen = new Set<string>();
  const out: Skill[] = [];
  for (const dir of skillDirs()) {
    for (const file of readdirSync(dir).sort()) {
      if (!file.endsWith(".md")) continue;
      const key = basename(file, ".md");
      if (seen.has(key)) continue;
      try {
        out.push(parse(join(dir, file)));
        seen.add(key);
      } catch {
        continue;
      }
    }
  }
  return out;
}

export function find(name: string): Skill | null {
  const target = (name ?? "").trim().toLowerCase();
  if (!target) return null;
  return discover().find((s) => s.key.toLowerCase() === target || s.name.toLowerCase() === target) ?? null;
}

/** 技能正文，超长截断（正文进上下文，得有上限） */
export function body(skill: Skill, max = BODY_MAX): string {
  const text = readFileSync(skill.path, "utf8").trim();
  return text.length > max ? `${text.slice(0, max)}\n…（技能过长已截断，原 ${text.length} 字符）` : text;
}

/** 系统提示里的技能清单 */
export function catalog(): string {
  const skills = discover();
  if (skills.length === 0) return "";
  return [
    "可用技能（需要时先用 use_skill 加载正文，再照它做）：",
    ...skills.map((s) => `- ${s.key}：${s.description}`),
  ].join("\n");
}

/** 写一个技能文件，返回 key */
function writeSkill(name: string, content: Buffer | string): string {
  const key = safeName(name.replace(/\.md$/i, ""));
  const dir = skillsDir();
  mkdirSync(dir, { recursive: true });
  writeFileSync(join(dir, `${key}.md`), content, "utf8");
  return key;
}

export interface InstallResult {
  keys: string[];
  skipped: string[];
}

/**
 * 装技能：本地 .md / 本地目录 / GitHub 仓库、子目录、单个 md 文件。
 * 只取 .md，装完立刻出现在 catalog() 里。
 */
export async function install(source: string, name?: string): Promise<InstallResult> {
  const src = (source ?? "").trim();
  if (!src) throw new Error("地址不能为空");

  // 本地文件
  if (existsSync(src) && statSync(src).isFile()) {
    if (!src.toLowerCase().endsWith(".md")) throw new Error("技能只能是 Markdown 文件");
    return { keys: [writeSkill(name ?? basename(src), readFileSync(src))], skipped: [] };
  }

  // 本地目录
  if (existsSync(src) && statSync(src).isDirectory()) {
    const keys: string[] = [];
    for (const file of readdirSync(src).sort()) {
      if (!file.toLowerCase().endsWith(".md")) continue;
      keys.push(writeSkill(name ? `${name}_${basename(file, ".md")}` : basename(file), readFileSync(join(src, file))));
    }
    if (keys.length === 0) throw new Error("目录里没有 .md 技能文件");
    return { keys, skipped: [] };
  }

  if (!src.includes("github.com")) throw new Error("只支持 GitHub 地址、本地目录或 .md 文件");
  return installFromGitHub(src, name);
}

async function installFromGitHub(source: string, name?: string): Promise<InstallResult> {
  const parts = source.replace(/\/+$/, "").split("github.com/")[1]?.split("/") ?? [];
  if (parts.length < 2) throw new Error("GitHub 地址格式不对，应形如 https://github.com/owner/repo");

  const repo = `${parts[0]}/${parts[1]}`;
  let branch: string | null = null;
  let subdir = "";

  if (parts.length >= 4 && (parts[2] === "tree" || parts[2] === "blob")) {
    branch = parts[3] ?? null;
    if (parts[2] === "blob") {
      // 单文件：直接取 raw
      const path = parts.slice(4).join("/");
      if (!path.toLowerCase().endsWith(".md")) throw new Error(`技能只能是 Markdown 文件：${path}`);
      const url = `https://raw.githubusercontent.com/${repo}/${branch}/${path}`;
      const resp = await fetch(url);
      if (!resp.ok) throw new Error(`下载失败 HTTP ${resp.status}`);
      const buf = Buffer.from(await resp.arrayBuffer());
      return { keys: [writeSkill(name ?? basename(path), buf)], skipped: [] };
    }
    if (parts.length >= 5) subdir = parts.slice(4).join("/");
  }

  const payload = await downloadRepo(repo, branch);
  const entries = readZip(payload, { suffixes: [".md"] });
  if (entries.size === 0) throw new Error("仓库里没有 .md 文件");

  // 定了子目录就只看那一段；否则只看最浅一层，避免把整个文档站装进来
  const tail = subdir ? `${subdir.replace(/\/+$/, "")}/` : "";
  const candidates = [...entries.keys()].filter((path) => (tail ? path.includes(`/${tail}`) || path.startsWith(tail) : true));

  const shallowest = candidates.map((p) => p.split("/").length).sort((a, b) => a - b)[0] ?? 0;
  const picked = candidates.filter((p) =>
    tail ? true : p.split("/").length === shallowest,
  );

  const keys: string[] = [];
  const skipped: string[] = [];
  for (const path of picked) {
    let rel = path;
    if (tail) {
      const idx = path.indexOf(`/${tail}`);
      rel = idx >= 0 ? path.slice(idx + 1 + tail.length) : path.slice(tail.length);
    } else {
      rel = path.split("/").slice(1).join("/"); // 剥掉 zip 顶层 repo-main/
    }
    const flat = rel.replace(/\.md$/i, "").split("/").join("_");
    const key = safeName(name ? `${name}_${flat}` : flat);
    if (keys.includes(key)) {
      skipped.push(path);
      continue;
    }
    writeSkill(key, entries.get(path) as Buffer);
    keys.push(key);
  }
  if (keys.length === 0) throw new Error("没有可安装的技能文件");
  return { keys, skipped };
}

async function downloadRepo(repo: string, branch: string | null): Promise<Buffer> {
  const refs = [branch, "main", "master"].filter(Boolean) as string[];
  let lastError = "";
  for (const ref of refs) {
    const url = `https://codeload.github.com/${repo}/zip/refs/heads/${ref}`;
    try {
      const resp = await fetch(url);
      if (!resp.ok) {
        lastError = `HTTP ${resp.status}`;
        continue;
      }
      return Buffer.from(await resp.arrayBuffer());
    } catch (err) {
      lastError = (err as Error).message;
    }
  }
  throw new Error(`下载仓库失败（试过 ${refs.join(" / ")}）：${repo} ${lastError}`);
}
