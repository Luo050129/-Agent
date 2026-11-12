"""测试用的假 MCP server：按行分隔的 JSON-RPC over stdio。

故意做得比真 server 更全面一点，好把客户端的边角都碰到：

    initialize        握手
    tools/list        工具清单（可用 --extra N 造一堆冗长工具，测预算）
    tools/call        正常返回 / 报错 / 超慢 / 返回 base64 图片 / 返回超长文本
    die               直接把进程干掉，测「server 崩了怎么办」

跑法：python fake_mcp_server.py [--extra N] [--stderr-noise]
"""

from __future__ import annotations

import argparse
import json
import sys
import time


def build_tools(extra: int) -> list[dict]:
    tools = [
        {
            "name": "echo",
            "description": "Echo the given text back. Useful for checking that the transport works.",
            "inputSchema": {
                "$schema": "http://json-schema.org/draft-07/schema#",
                "title": "echo",
                "type": "object",
                "properties": {
                    "text": {
                        "type": "string",
                        "title": "Text",
                        "description": "The text to echo back to the caller. Keep it short.",
                    }
                },
                "required": ["text"],
                "additionalProperties": False,
            },
        },
        {
            "name": "add",
            "description": "Add two numbers and return the sum.",
            "inputSchema": {
                "type": "object",
                "properties": {
                    "a": {"type": "number", "description": "first addend"},
                    "b": {"type": "number", "description": "second addend"},
                },
                "required": ["a", "b"],
            },
        },
        {
            "name": "boom",
            "description": "Always fails. Used to test the [ERR] path.",
            "inputSchema": {"type": "object", "properties": {}},
        },
        {
            "name": "sleep",
            "description": "Sleep for a number of seconds before answering.",
            "inputSchema": {
                "type": "object",
                "properties": {"seconds": {"type": "number", "description": "how long"}},
                "required": ["seconds"],
            },
        },
        {
            "name": "screenshot",
            "description": "Return a fake image block with base64 data.",
            "inputSchema": {"type": "object", "properties": {}},
        },
        {
            "name": "big",
            "description": "Return a very long text result.",
            "inputSchema": {"type": "object", "properties": {}},
        },
        {
            "name": "die",
            "description": "Kill the server process immediately.",
            "inputSchema": {"type": "object", "properties": {}},
        },
    ]

    # 一批啰嗦的工具：描述带完整段落、schema 带一堆无用关键字，专门用来量压缩率
    for index in range(extra):
        tools.append(
            {
                "name": f"noise_{index}",
                "description": (
                    "This is a deliberately verbose tool used to measure how much the schema "
                    "compactor can save. It does nothing at all. " * 2
                ),
                "inputSchema": {
                    "$schema": "http://json-schema.org/draft-07/schema#",
                    "title": f"noise_{index}",
                    "type": "object",
                    "properties": {
                        f"param_{j}": {
                            "type": "string",
                            "title": f"Param {j}",
                            "description": "A parameter that exists only to make this schema long. " * 2,
                            "default": "",
                        }
                        for j in range(4)
                    },
                    "required": [f"param_{index % 4}"],
                    "additionalProperties": False,
                },
            }
        )
    return tools


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--extra", type=int, default=0)
    parser.add_argument("--stderr-noise", action="store_true")
    args = parser.parse_args()

    tools = build_tools(args.extra)

    def send(message: dict) -> None:
        sys.stdout.write(json.dumps(message, ensure_ascii=False) + "\n")
        sys.stdout.flush()

    if args.stderr_noise:
        sys.stderr.write("fake server starting\n")
        sys.stderr.flush()

    for raw in sys.stdin:
        line = raw.strip()
        if not line:
            continue
        try:
            message = json.loads(line)
        except json.JSONDecodeError:
            continue

        method = message.get("method")
        request_id = message.get("id")

        if method == "initialize":
            send(
                {
                    "jsonrpc": "2.0",
                    "id": request_id,
                    "result": {
                        "protocolVersion": message.get("params", {}).get("protocolVersion"),
                        "capabilities": {"tools": {}},
                        "serverInfo": {"name": "fake", "version": "1.0"},
                    },
                }
            )
            continue

        if method in ("notifications/initialized", "notifications/cancelled"):
            continue  # 通知没有响应

        if method == "tools/list":
            send({"jsonrpc": "2.0", "id": request_id, "result": {"tools": tools}})
            continue

        if method == "tools/call":
            params = message.get("params") or {}
            name = params.get("name")
            arguments = params.get("arguments") or {}

            if name == "echo":
                result = {"content": [{"type": "text", "text": str(arguments.get("text", ""))}]}
            elif name == "add":
                total = float(arguments.get("a", 0)) + float(arguments.get("b", 0))
                result = {"content": [{"type": "text", "text": str(total)}]}
            elif name == "boom":
                result = {"content": [{"type": "text", "text": "坏了"}], "isError": True}
            elif name == "sleep":
                time.sleep(float(arguments.get("seconds", 1)))
                result = {"content": [{"type": "text", "text": "醒了"}]}
            elif name == "screenshot":
                result = {
                    "content": [
                        {"type": "text", "text": "截图如下"},
                        {"type": "image", "mimeType": "image/png", "data": "iVBORw0KGgo" * 500},
                        {"type": "resource", "resource": {"uri": "file:///tmp/a.html"}},
                    ]
                }
            elif name == "big":
                result = {"content": [{"type": "text", "text": "长" * 12000}]}
            elif name == "die":
                sys.exit(3)
            elif name.startswith("noise_"):
                # 噪声工具也要能调，否则测不了「超预算后用 mcp_call 兜底」
                result = {
                    "content": [
                        {
                            "type": "text",
                            "text": f"{name} 收到 {json.dumps(arguments, ensure_ascii=False, sort_keys=True)}",
                        }
                    ]
                }
            else:
                send(
                    {
                        "jsonrpc": "2.0",
                        "id": request_id,
                        "error": {"code": -32602, "message": f"未知工具 {name}"},
                    }
                )
                continue

            send({"jsonrpc": "2.0", "id": request_id, "result": result})
            continue

        if request_id is not None:
            send(
                {
                    "jsonrpc": "2.0",
                    "id": request_id,
                    "error": {"code": -32601, "message": f"未实现 {method}"},
                }
            )

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
