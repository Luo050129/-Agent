/** MCP：握手、工具发现、调用、压缩、错误处理、图片占位。 */

import assert from "node:assert/strict";
import { test } from "node:test";
import { mkdtempSync, rmSync, writeFileSync } from "node:fs";
import { tmpdir } from "node:os";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";
import { MCPClient, flatten } from "../src/mcp/client.ts";
import { MCPManager } from "../src/mcp/manager.ts";

const HERE = dirname(fileURLToPath(import.meta.url));
const SERVER = join(HERE, "fake-mcp-server.mjs");

function fakeConfig() {
  return { command: process.execPath, args: [SERVER], timeout: 20 };
}

test("握手后能列出工具", async () => {
  const client = new MCPClient("fake", fakeConfig());
  try {
    const tools = await client.start();
    const names = tools.map((t) => t["name"]);
    assert.deepEqual(names, ["echo", "boom", "shot"]);
  } finally {
    client.close();
  }
});

test("调工具拿到文本", async () => {
  const client = new MCPClient("fake", fakeConfig());
  try {
    await client.start();
    const out = await client.callTool("echo", { text: "喂" });
    assert.equal(out, "喂");
  } finally {
    client.close();
  }
});

test("server 报错转成 [ERR]", async () => {
  const client = new MCPClient("fake", fakeConfig());
  try {
    await client.start();
    const out = await client.callTool("boom", {});
    assert.ok(out.startsWith("[ERR]"), out);
    assert.ok(out.includes("假装出错"));
  } finally {
    client.close();
  }
});

test("图片只留一句占位，不进上下文", () => {
  const out = flatten({ content: [{ type: "image", mimeType: "image/png", data: "xxxx" }] });
  assert.ok(out.includes("[image image/png 已省略]"), out);
  assert.ok(!out.includes("xxxx"));
});

test("manager 把工具转成 OpenAI schema 并压缩", async () => {
  const dir = mkdtempSync(join(tmpdir(), "xu-mcp-"));
  const configPath = join(dir, "mcp.json");
  writeFileSync(
    configPath,
    JSON.stringify({ servers: { fake: { command: process.execPath, args: [SERVER], timeout: 20 } } }),
    "utf8",
  );
  const manager = new MCPManager(configPath, join(dir, "cache.json"));
  try {
    const schemas = await manager.load();
    assert.equal(schemas.length, 3);

    const echo = schemas.find((s) => (s["function"] as { name: string }).name === "mcp__fake__echo");
    assert.ok(echo, "工具名要带 mcp__server__ 前缀");
    const fn = echo?.["function"] as Record<string, unknown>;
    assert.ok(String(fn["description"]).length <= 91, "描述要截断");
    const params = (fn["parameters"] as Record<string, unknown>)["properties"] as Record<string, unknown>;
    assert.ok("text" in params);
    assert.equal((params["text"] as Record<string, string>)["type"], "string");
    assert.deepEqual((fn["parameters"] as Record<string, unknown>)["required"], ["text"]);

    assert.equal(manager.isMcpTool("mcp__fake__echo"), true);
    assert.equal(manager.isMcpTool("read_file"), false);
  } finally {
    manager.closeAll();
    rmSync(dir, { recursive: true, force: true });
  }
});

test("manager 能路由调用", async () => {
  const dir = mkdtempSync(join(tmpdir(), "xu-mcp-"));
  const configPath = join(dir, "mcp.json");
  writeFileSync(
    configPath,
    JSON.stringify({ servers: { fake: { command: process.execPath, args: [SERVER], timeout: 20 } } }),
    "utf8",
  );
  const manager = new MCPManager(configPath, join(dir, "cache.json"));
  try {
    await manager.load();
    assert.equal(await manager.call("mcp__fake__echo", { text: "路由通了" }), "路由通了");
  } finally {
    manager.closeAll();
    rmSync(dir, { recursive: true, force: true });
  }
});

test("未配置的 server 给出明确错误", async () => {
  const dir = mkdtempSync(join(tmpdir(), "xu-mcp-"));
  const manager = new MCPManager(join(dir, "none.json"), join(dir, "cache.json"));
  try {
    const out = await manager.call("mcp__ghost__tool", {});
    assert.ok(out.includes("[ERR]"), out);
    assert.ok(out.includes("未配置"), out);
  } finally {
    rmSync(dir, { recursive: true, force: true });
  }
});

test("启动失败不会拖垮 load()", async () => {
  const dir = mkdtempSync(join(tmpdir(), "xu-mcp-"));
  const configPath = join(dir, "mcp.json");
  writeFileSync(
    configPath,
    JSON.stringify({
      servers: {
        ghost: { command: "definitely-not-a-real-binary-xyz", args: [], timeout: 5 },
        fake: { command: process.execPath, args: [SERVER], timeout: 20 },
      },
    }),
    "utf8",
  );
  const manager = new MCPManager(configPath, join(dir, "cache.json"));
  try {
    const schemas = await manager.load();
    assert.equal(schemas.length, 3, "坏 server 跳过，好的照常加载");
    const status = manager.status();
    assert.ok(status.some((s) => s.name === "ghost" && s.error), "坏 server 要记录错误");
  } finally {
    manager.closeAll();
    rmSync(dir, { recursive: true, force: true });
  }
});
