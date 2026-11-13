/**
 * 文档读取：docx / xlsx / pdf / 纯文本。对应 Python 版 tools/documents.ts。
 *
 * 全部用标准库：
 *   docx —— zip 里取 word/document.xml，按段落拼 <w:t>
 *   xlsx —— zip 里读 sharedStrings + 工作表，按行列输出
 *   pdf  —— 解 FlateDecode 流，扫 BT/ET 文本块，认 Tj/TJ 操作符
 *   其他 —— 当文本读
 *
 * 能力边界如实写在这里，别假装能读样式、公式、扫描件。
 */

import { existsSync, readFileSync, statSync } from "node:fs";
import { extname } from "node:path";
import { inflateSync } from "node:zlib";
import { ERR } from "../markers.ts";
import { listZip, readZip } from "../zip.ts";
import { truncate } from "./util.ts";

const TEXT_EXTS = new Set([
  ".md", ".txt", ".csv", ".json", ".yaml", ".yml", ".xml", ".html", ".htm",
  ".py", ".ts", ".js", ".mjs", ".java", ".go", ".rs", ".c", ".h", ".cpp", ".sh",
  ".toml", ".ini", ".cfg", ".log", ".sql",
]);
const IMAGE_EXTS = new Set([".png", ".jpg", ".jpeg", ".gif", ".webp", ".bmp"]);

/** docx：一段一段取 w:t，段落用换行分隔 */
function readDocx(file: string): string {
  const zip = readZip(readFileSync(file), { prefix: "" });
  const xml = zip.get("word/document.xml");
  if (!xml) return `${ERR}不是合法的 docx（缺 word/document.xml）`;

  const text = xml.toString("utf8");
  const paragraphs = text.split(/<w:p[ >]/).slice(1);
  const lines: string[] = [];
  for (const para of paragraphs) {
    const runs = [...para.matchAll(/<w:t[^>]*>([\s\S]*?)<\/w:t>/g)].map((m) => decodeXml(m[1] ?? ""));
    if (runs.length > 0) lines.push(runs.join(""));
  }
  return lines.join("\n") || `${ERR}文档里没有可提取的文本`;
}

/** xlsx：共享字符串 + 第一张表的单元格 */
function readXlsx(file: string): string {
  const zip = readZip(readFileSync(file));
  const names = [...zip.keys()];

  const shared: string[] = [];
  const sharedFile = names.find((n) => n.endsWith("sharedStrings.xml"));
  if (sharedFile) {
    const xml = (zip.get(sharedFile) as Buffer).toString("utf8");
    for (const m of xml.matchAll(/<si[^>]*>([\s\S]*?)<\/si>/g)) {
      const parts = [...(m[1] ?? "").matchAll(/<t[^>]*>([\s\S]*?)<\/t>/g)].map((x) => decodeXml(x[1] ?? ""));
      shared.push(parts.join(""));
    }
  }

  const sheets = names.filter((n) => /xl\/worksheets\/sheet\d+\.xml$/.test(n)).sort();
  if (sheets.length === 0) return `${ERR}不是合法的 xlsx（没有工作表）`;

  const sheetFile = sheets[0] as string;
  const xml = (zip.get(sheetFile) as Buffer).toString("utf8");
  const rows: string[] = [];

  for (const rowMatch of xml.matchAll(/<row[^>]*>([\s\S]*?)<\/row>/g)) {
    const cells: string[] = [];
    for (const cell of (rowMatch[1] ?? "").matchAll(/<c\b([^>]*)>([\s\S]*?)<\/c>/g)) {
      const attrs = cell[1] ?? "";
      const body = cell[2] ?? "";
      const isShared = /t="s"/.test(attrs);
      const value = /<v[^>]*>([\s\S]*?)<\/v>/.exec(body)?.[1] ?? "";
      const inline = /<is[^>]*>([\s\S]*?)<\/is>/.exec(body)?.[1] ?? "";
      if (inline) {
        const parts = [...inline.matchAll(/<t[^>]*>([\s\S]*?)<\/t>/g)].map((x) => decodeXml(x[1] ?? ""));
        cells.push(parts.join(""));
      } else if (isShared) {
        cells.push(shared[Number(value)] ?? "");
      } else {
        cells.push(decodeXml(value));
      }
    }
    if (cells.some((c) => c.trim())) rows.push(cells.join("\t"));
  }

  const body = rows.slice(0, 200).join("\n");
  const sheetName = sheetFile.split("/").pop() ?? "sheet";
  return `[工作表 ${sheetName}，共 ${rows.length} 行]\n${body}${rows.length > 200 ? "\n...（仅显示前 200 行）" : ""}`;
}

/**
 * pdf：解压缩流 → 扫文本块。
 * 只做「能读的读出来」：编码是 ToUnicode 才好还原，扫描件和特殊字体做不到。
 */
function readPdf(file: string): string {
  const buf = readFileSync(file);
  const pieces: string[] = [];

  // 找所有 stream ... endstream，尝试 inflate
  const raw = buf.toString("latin1");
  for (const match of raw.matchAll(/stream\r?\n([\s\S]*?)endstream/g)) {
    const body = Buffer.from(match[1] ?? "", "latin1");
    let text = "";
    try {
      text = inflateSync(body).toString("latin1");
    } catch {
      continue; // 不是 flate 流（图像、字体），跳过
    }
    if (!/\bTj\b|\bTJ\b/.test(text)) continue;

    for (const block of text.matchAll(/BT([\s\S]*?)ET/g)) {
      for (const show of (block[1] ?? "").matchAll(/\((?:\\.|[^\\()])*\)\s*Tj?|\[[^\]]*\]\s*TJ/g)) {
        const token = show[0];
        const strings = [...token.matchAll(/\((?:\\.|[^\\()])*\)/g)].map((m) =>
          unescapePdf(m[0].slice(1, -1)),
        );
        if (strings.length > 0) pieces.push(strings.join(""));
      }
    }
  }

  const text = pieces.join(" ").replace(/\s+/g, " ").trim();
  if (!text) {
    return `${ERR}没解出文本：可能是扫描件（需要 OCR），或字体用了自定义 CID 编码。`;
  }
  return text;
}

function unescapePdf(text: string): string {
  return text
    .replace(/\\([nrtbf()\\])/g, (_all, ch: string) => {
      const map: Record<string, string> = { n: "\n", r: "\r", t: "\t", b: "\b", f: "\f", "(": "(", ")": ")", "\\": "\\" };
      return map[ch] ?? ch;
    })
    .replace(/\\([0-7]{1,3})/g, (_all, oct: string) => String.fromCharCode(Number.parseInt(oct, 8)));
}

function decodeXml(text: string): string {
  return text
    .replace(/&lt;/g, "<")
    .replace(/&gt;/g, ">")
    .replace(/&amp;/g, "&")
    .replace(/&quot;/g, '"')
    .replace(/&apos;/g, "'");
}

export function readDocument(args: Record<string, unknown>): string {
  const path = String(args["path"] ?? "").trim();
  if (!path) return `${ERR}缺少 path 参数`;
  if (!existsSync(path) || !statSync(path).isFile()) return `${ERR}文件不存在：${path}`;

  const ext = extname(path).toLowerCase();
  try {
    if (ext === ".docx") return truncate(readDocx(path));
    if (ext === ".xlsx") return truncate(readXlsx(path));
    if (ext === ".pdf") return truncate(readPdf(path));
    if (IMAGE_EXTS.has(ext)) {
      const size = statSync(path).size;
      return `[图片 ${ext.slice(1)}，${size} 字节] 读不了画面内容，只能告诉你尺寸和大小。`;
    }
    if (TEXT_EXTS.has(ext) || ext === "") {
      return truncate(readFileSync(path, "utf8"));
    }
    return `${ERR}不支持的文件类型：${ext}（支持 docx/xlsx/pdf/图片/常见文本）`;
  } catch (err) {
    return `${ERR}解析失败：${(err as Error).message}`;
  }
}

export const schemas: Array<Record<string, unknown>> = [
  {
    type: "function",
    function: {
      name: "read_document",
      description:
        "读取文档内容：支持 docx、xlsx、pdf、图片（只返回尺寸）和常见文本格式。读不了样式、公式和扫描件。",
      parameters: {
        type: "object",
        properties: { path: { type: "string", description: "文件路径" } },
        required: ["path"],
      },
    },
  },
];

export const handlers = { read_document: readDocument };
export const dangerous: string[] = [];

export { listZip };
