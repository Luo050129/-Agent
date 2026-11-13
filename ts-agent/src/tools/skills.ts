/**
 * 技能工具：use_skill / list_skills。对应 Python 版 tools/skills_tool.ts。
 *
 * 正文是按需加载的：清单常驻系统提示，正文只在模型主动要的时候进上下文。
 */

import * as skills from "../skills.ts";
import { ERR } from "../markers.ts";
import { truncate } from "./util.ts";

export function useSkill(args: Record<string, unknown>): string {
  const name = String(args["name"] ?? "").trim();
  const found = skills.find(name);
  if (!found) {
    const available = skills.discover().map((s) => s.key).join("、") || "（没有可用技能）";
    return `${ERR}没有这个技能：${name}。可用技能：${available}`;
  }
  return truncate(`[技能 ${found.key}｜${found.name}]\n${skills.body(found)}`, 8000);
}

export function listSkills(_args: Record<string, unknown>): string {
  const items = skills.discover();
  if (items.length === 0) return `${ERR}当前没有安装任何技能。把 .md 放进 skills/ 就会出现`;
  return items.map((s) => `- ${s.key}：${s.description}`).join("\n");
}

/** 装技能（用户主动要装时才调） */
export async function installSkill(args: Record<string, unknown>): Promise<string> {
  const source = String(args["source"] ?? "").trim();
  if (!source) return `${ERR}缺少 source 参数（GitHub 地址或本地路径）`;
  try {
    const result = await skills.install(source);
    const extra = result.skipped.length > 0 ? `（跳过重名 ${result.skipped.length} 个）` : "";
    return `已装 ${result.keys.length} 个技能：${result.keys.join("、")}${extra}`;
  } catch (err) {
    return `${ERR}装技能失败：${(err as Error).message}`;
  }
}

export const schemas: Array<Record<string, unknown>> = [
  {
    type: "function",
    function: {
      name: "use_skill",
      description: "加载一个技能的完整正文，然后按它的方法做事。系统提示里列了可用技能名。",
      parameters: {
        type: "object",
        properties: { name: { type: "string", description: "技能名（文件名或标题）" } },
        required: ["name"],
      },
    },
  },
  {
    type: "function",
    function: {
      name: "list_skills",
      description: "列出当前可用的技能及其一句话描述。",
      parameters: { type: "object", properties: {} },
    },
  },
  {
    type: "function",
    function: {
      name: "install_skill",
      description: "从 GitHub 仓库或本地路径安装技能（Markdown 方法文档）。",
      parameters: {
        type: "object",
        properties: { source: { type: "string", description: "GitHub 地址或本地路径" } },
        required: ["source"],
      },
    },
  },
];

export const handlers = {
  use_skill: useSkill,
  list_skills: listSkills,
  install_skill: installSkill,
};

export const dangerous = ["install_skill"];
