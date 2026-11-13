/** 文档读取：docx / xlsx / pdf / 文本 / 图片 / 错误路径。 */

import assert from "node:assert/strict";
import { test } from "node:test";
import { mkdtempSync, rmSync, writeFileSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { deflateSync } from "node:zlib";
import { readDocument } from "../src/tools/documents.ts";
import { makeZip } from "./zip-helper.ts";

function tempDir(): string {
  return mkdtempSync(join(tmpdir(), "xu-doc-"));
}

function docx(texts: string[]): Buffer {
  const body = texts.map((t) => `<w:p><w:r><w:t>${t}</w:t></w:r></w:p>`).join("");
  return makeZip({
    "[Content_Types].xml": "<Types/>",
    "word/document.xml": `<?xml version="1.0"?><w:document><w:body>${body}</w:body></w:document>`,
  });
}

test("docx 按段落提取文本", () => {
  const dir = tempDir();
  try {
    const file = join(dir, "a.docx");
    writeFileSync(file, docx(["第一段", "第二段有 &amp; 转义"]));
    const out = readDocument({ path: file });
    assert.ok(out.includes("第一段"), out);
    assert.ok(out.includes("第二段有 & 转义"), "XML 实体要还原");
    assert.equal(out.split("\n").length >= 2, true);
  } finally {
    rmSync(dir, { recursive: true, force: true });
  }
});

test("docx 缺 document.xml 时明确报错", () => {
  const dir = tempDir();
  try {
    const file = join(dir, "bad.docx");
    writeFileSync(file, makeZip({ "other.xml": "<x/>" }));
    const out = readDocument({ path: file });
    assert.ok(out.startsWith("[ERR]"), out);
    assert.ok(out.includes("word/document.xml"));
  } finally {
    rmSync(dir, { recursive: true, force: true });
  }
});

test("xlsx 读共享字符串与单元格", () => {
  const dir = tempDir();
  try {
    const file = join(dir, "a.xlsx");
    writeFileSync(
      file,
      makeZip({
        "xl/sharedStrings.xml": '<sst><si><t>姓名</t></si><si><t>得分</t></si></sst>',
        "xl/worksheets/sheet1.xml":
          '<worksheet><sheetData>' +
          '<row><c t="s"><v>0</v></c><c t="s"><v>1</v></c></row>' +
          "<row><c><v>42</v></c></row>" +
          "</sheetData></worksheet>",
      }),
    );
    const out = readDocument({ path: file });
    assert.ok(out.includes("姓名"), out);
    assert.ok(out.includes("得分"), out);
    assert.ok(out.includes("42"), out);
    assert.ok(out.includes("工作表"), "要标明是哪个表");
  } finally {
    rmSync(dir, { recursive: true, force: true });
  }
});

test("pdf 解不出文本时说明原因", () => {
  const dir = tempDir();
  try {
    const file = join(dir, "scan.pdf");
    writeFileSync(file, "%PDF-1.4\n% 扫描件没有文本层\n");
    const out = readDocument({ path: file });
    assert.ok(out.startsWith("[ERR]"), out);
    assert.ok(out.includes("扫描件") || out.includes("OCR"), out);
  } finally {
    rmSync(dir, { recursive: true, force: true });
  }
});

test("pdf 能解出 flate 流里的文本", () => {
  const dir = tempDir();
  try {
    const stream = "BT /F1 12 Tf (Hello PDF) Tj ET";
    const packed = deflateSync(Buffer.from(stream, "latin1"));
    const file = join(dir, "ok.pdf");
    writeFileSync(file, Buffer.concat([Buffer.from("%PDF-1.4\nstream\n", "latin1"), packed, Buffer.from("\nendstream\n", "latin1")]));
    const out = readDocument({ path: file });
    assert.ok(out.includes("Hello PDF"), out);
  } finally {
    rmSync(dir, { recursive: true, force: true });
  }
});

test("纯文本原样读", () => {
  const dir = tempDir();
  try {
    const file = join(dir, "note.md");
    writeFileSync(file, "# 标题\n正文", "utf8");
    assert.ok(readDocument({ path: file }).includes("# 标题"));
  } finally {
    rmSync(dir, { recursive: true, force: true });
  }
});

test("图片只给尺寸提示，不假装能看图", () => {
  const dir = tempDir();
  try {
    const file = join(dir, "pic.png");
    writeFileSync(file, Buffer.from([0x89, 0x50, 0x4e, 0x47]));
    const out = readDocument({ path: file });
    assert.ok(out.includes("读不了画面内容"), out);
  } finally {
    rmSync(dir, { recursive: true, force: true });
  }
});

test("不存在的文件与不支持的类型都报错", () => {
  const dir = tempDir();
  try {
    assert.ok(readDocument({ path: join(dir, "nope.docx") }).startsWith("[ERR]"));
    const weird = join(dir, "x.weird");
    writeFileSync(weird, "x");
    const out = readDocument({ path: weird });
    assert.ok(out.startsWith("[ERR]"), out);
    assert.ok(out.includes("不支持的文件类型"));
  } finally {
    rmSync(dir, { recursive: true, force: true });
  }
});
