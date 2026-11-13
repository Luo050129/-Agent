/**
 * 测试用的假 MCP server：行分隔 JSON-RPC，零依赖。
 * 提供三个工具：echo（回显）、boom（永远报错）、shot（返回图片）。
 */

import { createInterface } from "node:readline";

const TOOLS = [
  {
    name: "echo",
    description: "原样返回输入文本，用来验证链路",
    inputSchema: {
      type: "object",
      properties: { text: { type: "string", description: "要返回的文本" } },
      required: ["text"],
    },
  },
  {
    name: "boom",
    description: "总是失败，用来验证错误处理",
    inputSchema: { type: "object", properties: {} },
  },
  {
    name: "shot",
    description: "返回一张图，用来验证图片占位",
    inputSchema: { type: "object", properties: {} },
  },
];

const lines = createInterface({ input: process.stdin });

lines.on("line", (line) => {
  const text = line.trim();
  if (!text) return;
  let msg;
  try {
    msg = JSON.parse(text);
  } catch {
    return;
  }
  if (msg.id === undefined) return; // 通知，不用回

  let result = {};
  if (msg.method === "initialize") {
    result = {
      protocolVersion: "2024-11-05",
      capabilities: { tools: {} },
      serverInfo: { name: "fake-mcp", version: "1.0" },
    };
  } else if (msg.method === "tools/list") {
    result = { tools: TOOLS };
  } else if (msg.method === "tools/call") {
    const name = msg.params?.name;
    if (name === "echo") {
      result = { content: [{ type: "text", text: String(msg.params?.arguments?.text ?? "") }] };
    } else if (name === "boom") {
      result = { isError: true, content: [{ type: "text", text: "假装出错" }] };
    } else {
      result = { content: [{ type: "image", mimeType: "image/png", data: "iVBORw0KGgo=" }] };
    }
  }

  process.stdout.write(`${JSON.stringify({ jsonrpc: "2.0", id: msg.id, result })}\n`);
});

// server 退出时把管道关掉，避免测试挂住
lines.on("close", () => process.exit(0));
