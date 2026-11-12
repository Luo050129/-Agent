"""多协议适配测试：一套接口，四种原生协议。

用本地假服务端，验证两件事：
  1. 请求翻译对不对（对方的字段、鉴权头、工具定义形状）
  2. 流解析对不对（文本与工具调用能从各家的事件格式里拼回来）

`python tests/test_protocols.py` 直接跑，不联网。
"""

from __future__ import annotations

import json
import os
import sys
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from miniagent.model import LLM  # noqa: E402

LAST: dict[str, dict] = {}


def sse(*blocks: str) -> bytes:
    return "".join(blocks).encode()


def data_block(obj: dict) -> str:
    return f"data: {json.dumps(obj, ensure_ascii=False)}\n\n"


def event_block(name: str, obj: dict) -> str:
    return f"event: {name}\ndata: {json.dumps(obj, ensure_ascii=False)}\n\n"


OPENAI_SSE = sse(
    data_block({"choices": [{"delta": {"content": "你好"}}]}),
    data_block(
        {
            "choices": [
                {
                    "delta": {
                        "tool_calls": [
                            {
                                "index": 0,
                                "id": "call_a",
                                "function": {"name": "list_dir", "arguments": '{"pa'},
                            }
                        ]
                    }
                }
            ]
        }
    ),
    data_block({"choices": [{"delta": {"tool_calls": [{"index": 0, "function": {"arguments": 'th": "."}'}}]}}]}),
    data_block({"choices": [{"delta": {}, "finish_reason": "tool_calls"}]}),
    data_block({"usage": {"prompt_tokens": 10, "completion_tokens": 5}}),
    "data: [DONE]\n\n",
)

ANTHROPIC_SSE = sse(
    event_block("message_start", {"type": "message_start", "message": {"usage": {"input_tokens": 12}}}),
    event_block("content_block_delta",
                {"type": "content_block_delta", "index": 0,
                 "delta": {"type": "text_delta", "text": "你好"}}),
    event_block("content_block_start",
                {"type": "content_block_start", "index": 1,
                 "content_block": {"type": "tool_use", "id": "toolu_1", "name": "list_dir"}}),
    event_block("content_block_delta",
                {"type": "content_block_delta", "index": 1,
                 "delta": {"type": "input_json_delta", "partial_json": '{"path":'}}),
    event_block("content_block_delta",
                {"type": "content_block_delta", "index": 1,
                 "delta": {"type": "input_json_delta", "partial_json": ' "."}'}}),
    event_block("message_delta",
                {"type": "message_delta", "delta": {"stop_reason": "tool_use"}, "usage": {"output_tokens": 7}}),
    event_block("message_stop", {"type": "message_stop"}),
)

GEMINI_SSE = sse(
    data_block({"candidates": [{"content": {"parts": [{"text": "你好"}]}}]}),
    data_block(
        {
            "candidates": [
                {
                    "content": {"parts": [{"functionCall": {"name": "list_dir", "args": {"path": "."}}}]},
                    "finishReason": "STOP",
                }
            ],
            "usageMetadata": {"promptTokenCount": 9, "candidatesTokenCount": 4},
        }
    ),
)

RESPONSES_SSE = sse(
    event_block("response.output_item.added",
                {"type": "response.output_item.added",
                 "item": {"id": "fc_1", "type": "function_call", "call_id": "call_r", "name": "list_dir"}}),
    event_block("response.output_text.delta", {"type": "response.output_text.delta", "delta": "你好"}),
    event_block("response.function_call_arguments.delta",
                {"type": "response.function_call_arguments.delta", "item_id": "fc_1",
                 "delta": '{"path": "."}'}),
    event_block("response.completed",
                {"type": "response.completed",
                 "response": {"usage": {"input_tokens": 8, "output_tokens": 3}}}),
)


class Handler(BaseHTTPRequestHandler):
    def log_message(self, *args):
        pass

    def do_POST(self):
        length = int(self.headers.get("Content-Length", 0))
        raw = self.rfile.read(length)
        try:
            body = json.loads(raw or b"{}")
        except json.JSONDecodeError:
            body = {}

        path = self.path
        LAST[path] = {"headers": {k.lower(): v for k, v in self.headers.items()}, "body": body}

        if "/chat/completions" in path:
            payload = OPENAI_SSE
        elif path.endswith("/messages"):
            payload = ANTHROPIC_SSE
        elif "streamGenerateContent" in path:
            payload = GEMINI_SSE
        elif path.endswith("/responses"):
            payload = RESPONSES_SSE
        else:
            self.send_response(404)
            self.end_headers()
            return

        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream")
        self.end_headers()
        self.wfile.write(payload)
        self.wfile.flush()


MESSAGES = [
    {"role": "system", "content": "你是助手"},
    {"role": "user", "content": "看看目录"},
    {
        "role": "assistant",
        "content": "我看看",
        "tool_calls": [
            {"id": "call_1", "type": "function",
             "function": {"name": "list_dir", "arguments": '{"path": "."}'}}
        ],
    },
    {"role": "tool", "tool_call_id": "call_1", "content": "a.py\nb.py"},
    {"role": "user", "content": "说说有几个文件"},
]

TOOLS = [
    {
        "type": "function",
        "function": {
            "name": "list_dir",
            "description": "列目录",
            "parameters": {"type": "object", "properties": {"path": {"type": "string"}}},
        },
    }
]


def find_key(d: dict, key: str, path: str = "") -> tuple[str, object] | None:
    """在抓到的请求体里找某个路径，用于跨协议断言。"""
    if isinstance(d, dict):
        for k, v in d.items():
            if k == key:
                return (f"{path}.{k}", v)
            found = find_key(v, key, f"{path}.{k}")
            if found:
                return found
    elif isinstance(d, list):
        for i, v in enumerate(d):
            found = find_key(v, key, f"{path}[{i}]")
            if found:
                return found
    return None


def main() -> int:
    print("[protocols] 开始")
    server = HTTPServer(("127.0.0.1", 0), Handler)
    port = server.server_address[1]
    threading.Thread(target=server.serve_forever, daemon=True).start()
    base = f"http://127.0.0.1:{port}"

    checks: list[tuple[str, bool]] = []

    # ---------------- OpenAI 兼容 ----------------
    llm = LLM("DeepSeek V4 Flash", base_url=f"{base}/v1", api_key_value="k")
    text, calls = llm.stream(MESSAGES, TOOLS)
    req = LAST.get("/v1/chat/completions", {})
    body = req.get("body", {})
    checks += [
        ("openai 打到了 chat/completions", bool(body)),
        ("openai 鉴权是 Bearer", req.get("headers", {}).get("authorization") == "Bearer k"),
        ("openai 透传 messages", len(body.get("messages") or []) == 5),
        ("openai 带 stream_options", (body.get("stream_options") or {}).get("include_usage") is True),
        ("openai 文本拼接", text == "你好"),
        ("openai 工具名拼接", calls and calls[0]["function"]["name"] == "list_dir"),
        ("openai 工具参数拼接", calls and json.loads(calls[0]["function"]["arguments"])["path"] == "."),
        ("openai 记住用量", llm.last_usage is not None and llm.last_usage.input_tokens == 10),
    ]

    # ---------------- Anthropic ----------------
    llm = LLM("Claude Sonnet 4.5", base_url=f"{base}/v1", api_key_value="k")
    text, calls = llm.stream(MESSAGES, TOOLS)
    req = LAST.get("/v1/messages", {})
    body = req.get("body", {})
    messages = body.get("messages") or []
    checks += [
        ("anthropic 打到了 /messages", bool(body)),
        ("anthropic 用 x-api-key", req.get("headers", {}).get("x-api-key") == "k"),
        ("anthropic 带版本头", "anthropic-version" in req.get("headers", {})),
        ("anthropic 没走 Bearer", "Authorization" not in req.get("headers", {})),
        ("anthropic system 提到顶层", body.get("system") == "你是助手"),
        ("anthropic 工具用 input_schema", (body.get("tools") or [{}])[0].get("input_schema") is not None),
        ("anthropic 工具结果变 tool_result",
         any(block.get("type") == "tool_result" for m in messages for block in (m.get("content") or []))),
        ("anthropic 工具调用变 tool_use",
         any(block.get("type") == "tool_use" for m in messages for block in (m.get("content") or []))),
        ("anthropic 角色交替（无连续同角色）",
         all(messages[i]["role"] != messages[i + 1]["role"] for i in range(len(messages) - 1))),
        ("anthropic 文本拼接", text == "你好"),
        ("anthropic 工具参数靠片段拼接", calls and json.loads(calls[0]["function"]["arguments"])["path"] == "."),
        ("anthropic 工具 id 保留", calls and calls[0]["id"] == "toolu_1"),
    ]

    # ---------------- Gemini ----------------
    llm = LLM("Gemini 2.5 Flash", base_url=f"{base}/v1beta", api_key_value="k")
    text, calls = llm.stream(MESSAGES, TOOLS)
    gemini_path = next((p for p in LAST if "streamGenerateContent" in p), "")
    req = LAST.get(gemini_path, {})
    body = req.get("body", {})
    contents = body.get("contents") or []
    checks += [
        ("gemini 路径带模型与动作", ":streamGenerateContent" in gemini_path),
        ("gemini 用 alt=sse", "alt=sse" in gemini_path),
        ("gemini 用 x-goog-api-key", req.get("headers", {}).get("x-goog-api-key") == "k"),
        ("gemini 角色是 user/model", {c["role"] for c in contents} <= {"user", "model"}),
        ("gemini system 变 systemInstruction", bool(body.get("systemInstruction"))),
        ("gemini 工具用 functionDeclarations",
         bool((body.get("tools") or [{}])[0].get("functionDeclarations"))),
        ("gemini 工具结果变 functionResponse",
         any("functionResponse" in part for c in contents for part in c["parts"])),
        ("gemini functionResponse 带函数名",
         any(part.get("functionResponse", {}).get("name") == "list_dir"
             for c in contents for part in c["parts"] if "functionResponse" in part)),
        ("gemini 文本拼接", text == "你好"),
        ("gemini functionCall 解析", calls and calls[0]["function"]["name"] == "list_dir"),
        ("gemini 参数是完整 JSON", calls and json.loads(calls[0]["function"]["arguments"])["path"] == "."),
    ]

    # ---------------- OpenAI Responses ----------------
    llm = LLM("GPT-5", base_url=f"{base}/v1", api_key_value="k")
    text, calls = llm.stream(MESSAGES, TOOLS)
    req = LAST.get("/v1/responses", {})
    body = req.get("body", {})
    items = body.get("input") or []
    checks += [
        ("responses 打到了 /responses", bool(body)),
        ("responses 用 input 数组", isinstance(items, list) and bool(items)),
        ("responses 工具是扁平定义", (body.get("tools") or [{}])[0].get("type") == "function"),
        ("responses 工具带 name 而非嵌套", "name" in (body.get("tools") or [{}])[0]),
        ("responses 工具结果变 function_call_output",
         any(item.get("type") == "function_call_output" for item in items)),
        ("responses 工具调用变 function_call",
         any(item.get("type") == "function_call" for item in items)),
        ("responses 文本用 input_text", items[0].get("content", [{}])[0].get("type") == "input_text"),
        ("responses 文本拼接", text == "你好"),
        ("responses 工具调用解析", calls and calls[0]["function"]["name"] == "list_dir"),
        ("responses 参数增量拼接", calls and json.loads(calls[0]["function"]["arguments"])["path"] == "."),
    ]

    server.shutdown()

    for label, ok in checks:
        print(f"  {'PASS' if ok else 'FAIL'}  {label}")
    ok_all = all(ok for _, ok in checks)
    print(f"[protocols] {'全部通过' if ok_all else '存在失败项'}")
    return 0 if ok_all else 1


if __name__ == "__main__":
    raise SystemExit(main())
