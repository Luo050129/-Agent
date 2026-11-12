"""端到端测试：本地假 OpenAI 服务 + 真 HTTP + 真工具执行。

重点验证模型层的 SSE 增量解析，尤其是 tool_calls 的 arguments 被拆成多个 chunk 时的拼接。
不需要 API Key，`python tests/test_e2e.py` 直接跑。
"""

from __future__ import annotations

import json
import os
import sys
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from miniagent.core import Session, run  # noqa: E402
from miniagent.model import LLM  # noqa: E402


class Handler(BaseHTTPRequestHandler):
    def log_message(self, *args):
        pass

    def do_POST(self):
        length = int(self.headers.get("Content-Length", 0))
        body = json.loads(self.rfile.read(length) or b"{}")
        assert body["stream"] is True, "客户端应当使用流式"
        assert body.get("tools"), "应当带上工具定义"
        assert self.headers.get("Authorization", "").startswith("Bearer "), "应当带鉴权头"

        has_tool_result = any(m.get("role") == "tool" for m in body["messages"])

        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream")
        self.end_headers()

        def sse(obj):
            self.wfile.write(f"data: {json.dumps(obj, ensure_ascii=False)}\n\n".encode())
            self.wfile.flush()

        def delta(d):
            sse({"choices": [{"index": 0, "delta": d}]})

        if not has_tool_result:
            delta({"content": "我先跑一下命令确认。"})
            # 函数名与参数故意拆成三段，考验增量拼接
            delta({"tool_calls": [{"index": 0, "id": "call_x", "type": "function",
                                   "function": {"name": "run_", "arguments": '{"comm'}}]})
            delta({"tool_calls": [{"index": 0, "function": {"name": "shell", "arguments": 'and": "echo '}}]})
            delta({"tool_calls": [{"index": 0, "function": {"arguments": 'e2e-ok"}'}}]})
        else:
            tool_msg = [m for m in body["messages"] if m.get("role") == "tool"][-1]
            assert "e2e-ok" in tool_msg["content"], f"工具结果没回灌：{tool_msg}"
            delta({"content": "命令输出是 "})
            delta({"content": "e2e-ok"})

        self.wfile.write(b"data: [DONE]\n\n")
        self.wfile.flush()


def main() -> int:
    server = HTTPServer(("127.0.0.1", 0), Handler)
    port = server.server_address[1]
    threading.Thread(target=server.serve_forever, daemon=True).start()

    llm = LLM(model="mock", base_url=f"http://127.0.0.1:{port}/v1", api_key_value="test-key")
    session = Session(system="测试")
    session.add_user("跑一条命令")

    print("[e2e] 开始")
    # 关掉出口自评：这里验的是流式解析与工具回灌，假服务端没实现自评分支
    answer = run(llm, session.messages, max_steps=5, self_check=False)
    server.shutdown()

    checks = [
        ("拿到最终答复", "e2e-ok" in answer),
        ("工具调用被正确拼接并执行", any(
            m.get("role") == "tool" and "e2e-ok" in m.get("content", "") for m in session.messages
        )),
        ("assistant 消息带 tool_calls 历史", any(
            m.get("role") == "assistant" and m.get("tool_calls") for m in session.messages
        )),
    ]
    for label, ok in checks:
        print(f"  {'PASS' if ok else 'FAIL'}  {label}")
    ok_all = all(ok for _, ok in checks)
    print(f"[e2e] {'全部通过' if ok_all else '存在失败项'}")
    return 0 if ok_all else 1


if __name__ == "__main__":
    raise SystemExit(main())
