/** zip 读取、技能发现/安装、插件加载。zip 用测试内自制构造器，不引依赖。 */

import assert from "node:assert/strict";
import { test } from "node:test";
import { deflateRawSync } from "node:zlib";
import { existsSync, mkdirSync, mkdtempSync, rmSync, writeFileSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { list } from "../src/plugins.ts";
import { install, discover, find, body, catalog } from "../src/skills.ts";
import { readZip, listZip } from "../src/zip.ts";

/** 最小 zip 构造器：只写 store / deflate 两种，够测读取 */
function makeZip(files: Record<string, string>): Buffer {
  const locals: Buffer[] = [];
  const centrals: Buffer[] = [];
  let offset = 0;

  for (const [name, content] of Object.entries(files)) {
    const data = Buffer.from(content, "utf8");
    const nameBuf = Buffer.from(name, "utf8");
    const deflated = deflateRawSync(data);
    const useDeflate = deflated.length < data.length;
    const payload = useDeflate ? deflated : data;
    const method = useDeflate ? 8 : 0;

    const local = Buffer.alloc(30 + nameBuf.length);
    local.writeUInt32LE(0x04034b50, 0);
    local.writeUInt16LE(20, 4);
    local.writeUInt16LE(method, 8);
    local.writeUInt32LE(payload.length, 18);
    local.writeUInt32LE(data.length, 22);
    local.writeUInt16LE(nameBuf.length, 26);
    nameBuf.copy(local, 30);
    locals.push(local, payload);

    const central = Buffer.alloc(46 + nameBuf.length);
    central.writeUInt32LE(0x02014b50, 0);
    central.writeUInt16LE(20, 4);
    central.writeUInt16LE(20, 6);
    central.writeUInt16LE(method, 10);
    central.writeUInt32LE(payload.length, 20);
    central.writeUInt32LE(data.length, 24);
    central.writeUInt16LE(nameBuf.length, 28);
    central.writeUInt32LE(offset, 42);
    nameBuf.copy(central, 46);
    centrals.push(central);

    offset += local.length + payload.length;
  }

  const centralBuf = Buffer.concat(centrals);
  const eocd = Buffer.alloc(22);
  eocd.writeUInt32LE(0x06054b50, 0);
  eocd.writeUInt16LE(Object.keys(files).length, 8);
  eocd.writeUInt16LE(Object.keys(files).length, 10);
  eocd.writeUInt32LE(centralBuf.length, 12);
  eocd.writeUInt32LE(offset, 16);

  return Buffer.concat([...locals, centralBuf, eocd]);
}

test("zip 能列出与解出文件", () => {
  const buf = makeZip({
    "repo-main/README.md": "# 标题\n正文若干内容",
    "repo-main/skills/review.md": "# review\n> 审查方法",
    "repo-main/app.js": "console.log(1)",
  });

  assert.deepEqual(
    listZip(buf).map((e) => e.name).sort(),
    ["repo-main/README.md", "repo-main/app.js", "repo-main/skills/review.md"],
  );

  const md = readZip(buf, { suffixes: [".md"], prefix: "repo-main/" });
  assert.equal(md.size, 2);
  assert.ok(md.get("README.md")?.toString("utf8").includes("标题"));
});

test("zip 挡路径穿越", () => {
  const buf = makeZip({ "../escape.md": "坏东西" });
  const out = readZip(buf, { suffixes: [".md"] });
  assert.equal(out.size, 0, "带 .. 的条目必须丢掉");
});

test("坏 zip 报错而不是崩", () => {
  assert.throws(() => listZip(Buffer.from("not a zip at all")), /不是合法的 zip/);
});

test("技能：发现、查找、正文、清单", () => {
  const dir = mkdtempSync(join(tmpdir(), "xu-skills-"));
  const old = process.env["XU_SKILLS_DIR"];
  try {
    writeFileSync(join(dir, "review.md"), "# 代码审查\n\n> 审代码时按三层说\n\n正文方法", "utf8");
    writeFileSync(join(dir, "notes.txt"), "不是技能", "utf8");
    process.env["XU_SKILLS_DIR"] = dir;

    const found = discover();
    assert.equal(found.length, 1, JSON.stringify(found.map((s) => s.key)));
    assert.equal(found[0]?.name, "代码审查");
    assert.ok(found[0]?.description.includes("三层"));
    assert.ok(body(found[0]!).includes("正文方法"));
    assert.ok(find("review") !== null);
    assert.ok(find("代码审查") !== null);
    assert.equal(find("不存在"), null);
    assert.ok(catalog().includes("review"));
  } finally {
    if (old === undefined) delete process.env["XU_SKILLS_DIR"];
    else process.env["XU_SKILLS_DIR"] = old;
    rmSync(dir, { recursive: true, force: true });
  }
});

test("技能：本地目录安装后能立刻用", async () => {
  const dir = mkdtempSync(join(tmpdir(), "xu-skill-src-"));
  const target = mkdtempSync(join(tmpdir(), "xu-skill-dst-"));
  const old = process.env["XU_SKILLS_DIR"];
  try {
    writeFileSync(join(dir, "alpha.md"), "# alpha\n\n> 技能 A", "utf8");
    writeFileSync(join(dir, "beta.md"), "# beta\n\n> 技能 B", "utf8");
    process.env["XU_SKILLS_DIR"] = target; // 装到临时目录，别污染真实 skills/

    const result = await install(dir);
    assert.equal(result.keys.length, 2);
    assert.ok(result.keys.includes("alpha"));
    assert.ok(catalog().includes("alpha"), "装完立刻出现在清单里");
    assert.ok(existsSync(join(target, "alpha.md")), "文件要真的落到目标目录");
  } finally {
    if (old === undefined) delete process.env["XU_SKILLS_DIR"];
    else process.env["XU_SKILLS_DIR"] = old;
    rmSync(dir, { recursive: true, force: true });
    rmSync(target, { recursive: true, force: true });
  }
});

test("技能：非 md 文件被拒", async () => {
  const dir = mkdtempSync(join(tmpdir(), "xu-skill-bad-"));
  try {
    const file = join(dir, "x.txt");
    writeFileSync(file, "nope", "utf8");
    await assert.rejects(() => install(file), /只能是 Markdown/);
  } finally {
    rmSync(dir, { recursive: true, force: true });
  }
});

test("插件：没有插件目录时返回空", () => {
  const items = list();
  assert.ok(Array.isArray(items));
  for (const item of items) assert.ok(item.name);
});

test("插件：本地目录安装、加载、执行", async () => {
  const src = mkdtempSync(join(tmpdir(), "xu-plugin-src-"));
  const name = `probe_${Date.now()}`;
  try {
    writeFileSync(
      join(src, "plugin.mjs"),
      [
        "export const SCHEMAS = [{",
        '  type: "function",',
        `  function: { name: "${name}_echo", description: "回显", parameters: { type: "object", properties: { t: { type: "string" } } } },`,
        "}];",
        `export const HANDLERS = { "${name}_echo": (args) => \`echo:\${args.t}\` };`,
        "export const META = { description: '测试插件' };",
      ].join("\n"),
      "utf8",
    );

    const { install: installPlugin, loadTools, PLUGINS_DIR } = await import("../src/plugins.ts");
    const info = await installPlugin(src, name);
    assert.equal(info.name, name);
    assert.ok(info.tools.includes(`${name}_echo`));

    const loaded = await loadTools();
    assert.ok(loaded.handlers[`${name}_echo`], JSON.stringify(Object.keys(loaded.handlers)));
    assert.equal(loaded.handlers[`${name}_echo`]?.({ t: "hi" }), "echo:hi");

    const dir = join(PLUGINS_DIR, name);
    assert.ok(existsSync(dir));
  } finally {
    rmSync(src, { recursive: true, force: true });
    // 清掉刚装的插件目录
    const { PLUGINS_DIR } = await import("../src/plugins.ts");
    rmSync(join(PLUGINS_DIR, name), { recursive: true, force: true });
  }
});

test("插件：目录缺入口文件时给出错误", async () => {
  const src = mkdtempSync(join(tmpdir(), "xu-plugin-bad-"));
  try {
    mkdirSync(join(src, "empty"), { recursive: true });
    const { install: installPlugin } = await import("../src/plugins.ts");
    await assert.rejects(() => installPlugin(src), /没有 plugin\.mjs/);
  } finally {
    rmSync(src, { recursive: true, force: true });
  }
});
