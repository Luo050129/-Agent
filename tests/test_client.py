"""模型层韧性测试：重试、超时、不可重试的错误。

本地起假服务端，`python tests/test_client.py` 直接跑，不需要网络。
"""

from __future__ import annotations

import json
import os
import sys
import threading
import time
from http.server import BaseHTTPRequestHandler, HTTPServer

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from miniagent.model import LLM, LLMError  # noqa: E402

FAIL_TIMES = {"remaining": 0}
MODE = {"value": "flaky"}


class Handler(BaseHTTPRequestHandler):
    def log_message(self, *args):
        pass

    def do_POST(self):
        length = int(self.headers.get("Content-Length", 0))
        self.rfile.read(length)

        if MODE["value"] == "auth":
            self.send_response(401)
            self.end_headers()
            self.wfile.write(b'{"error":"invalid api key"}')
            return

        if MODE["value"] == "flaky" and FAIL_TIMES["remaining"] > 0:
            FAIL_TIMES["remaining"] -= 1
            self.send_response(500)
            self.end_headers()
            self.wfile.write(b'{"error":"server boom"}')
            return

        if MODE["value"] == "stall":
            self.send_response(200)
            self.send_header("Content-Type", "text/event-stream")
            self.end_headers()
            time.sleep(3)  # 客户端读超时会被触发
            self.wfile.write(b"data: [DONE]\n\n")
            return

        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream")
        self.end_headers()
        for piece in ("重试", "成功"):
            payload = {"choices": [{"delta": {"content": piece}}]}
            self.wfile.write(f"data: {json.dumps(payload, ensure_ascii=False)}\n\n".encode())
            self.wfile.flush()
        self.wfile.write(b"data: [DONE]\n\n")
        self.wfile.flush()


def main() -> int:
    print("[client] 开始")
    server = HTTPServer(("127.0.0.1", 0), Handler)
    port = server.server_address[1]
    threading.Thread(target=server.serve_forever, daemon=True).start()

    url = f"http://127.0.0.1:{port}/v1"
    checks: list[tuple[str, bool]] = []

    # 1) 5xx 两次后成功：应该自动重试
    MODE["value"] = "flaky"
    FAIL_TIMES["remaining"] = 2
    llm = LLM(model="mock", base_url=url, api_key_value="k", retries=2, timeout=5, call_deadline=5)
    text, calls = llm.stream([{"role": "user", "content": "hi"}])
    checks.append(("5xx 自动重试后成功", text == "重试成功"))
    checks.append(("记录了重试次数", llm.last_attempts == 3))

    # 2) 401 不该重试
    MODE["value"] = "auth"
    llm = LLM(model="mock", base_url=url, api_key_value="k", retries=2, timeout=5, call_deadline=5)
    try:
        llm.stream([{"role": "user", "content": "hi"}])
        checks.append(("401 直接报错", False))
    except LLMError as exc:
        checks.append(("401 直接报错", "401" in str(exc)))
    checks.append(("401 不重试", llm.last_attempts == 1))

    # 3) 服务端一直不回：读超时
    MODE["value"] = "stall"
    llm = LLM(model="mock", base_url=url, api_key_value="k", retries=1, timeout=1, call_deadline=2)
    start = time.time()
    try:
        llm.stream([{"role": "user", "content": "hi"}])
        checks.append(("卡住会超时", False))
    except LLMError as exc:
        checks.append(("卡住会超时", "超时" in str(exc) or "时限" in str(exc)))
    checks.append(("没有无限等下去", time.time() - start < 20))

    # 4) 网络不通
    llm = LLM(model="mock", base_url="http://127.0.0.1:1/v1", api_key_value="k", retries=1, timeout=1)
    try:
        llm.stream([{"role": "user", "content": "hi"}])
        checks.append(("连不上会报错", False))
    except LLMError as exc:
        checks.append(("连不上会报错", "连不上" in str(exc) or "失败" in str(exc)))

    server.shutdown()

    for label, ok in checks:
        print(f"  {'PASS' if ok else 'FAIL'}  {label}")
    ok_all = all(ok for _, ok in checks)
    print(f"[client] {'全部通过' if ok_all else '存在失败项'}")
    return 0 if ok_all else 1


if __name__ == "__main__":
    raise SystemExit(main())
