"""MCP 传输层：按行分隔的 JSON-RPC over stdio。

一个 MCP server 就是一个子进程：我们往它 stdin 写一行 JSON，它往 stdout
吐一行 JSON。没有 HTTP、没有 WebSocket，也不需要任何第三方库。

三条实现要点：

1. **必须在后台线程读 stdout**。server 可能随时吐消息（进度通知、日志），
   主线程只在等自己那条响应；不读的话管道缓冲满了子进程会卡死。
2. **stderr 也要抽干**。很多 server 一启动就往 stderr 打日志，不读同样会堵。
3. **按 id 派发**。多线程共用一个管道时，响应未必按发出顺序回来。

这里只管「连得上、发得出、收得到」，工具语义在 manager.py。
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import threading
from typing import Any

PROTOCOL_VERSION = "2024-11-05"
CLIENT_INFO = {"name": "xu-agent", "version": "1.0"}


class MCPError(RuntimeError):
    """MCP 调用失败（连不上、协议错、server 报错）。"""


class MCPTimeout(MCPError):
    """等响应超时。"""


class MCPGone(MCPError):
    """子进程已经退出。"""


def _no_window() -> int:
    """Windows 下别弹黑框。"""
    if sys.platform == "win32":
        return getattr(subprocess, "CREATE_NO_WINDOW", 0x08000000)
    return 0


class StdioClient:
    """一个 server 一条连接。

    用法：
        client = StdioClient("node", ["server.js"])
        client.start()                 # 握手
        client.request("tools/list")   # 之后随便发
        client.close()
    """

    def __init__(
        self,
        command: str,
        args: list[str] | None = None,
        *,
        name: str = "mcp",
        env: dict | None = None,
        cwd: str | None = None,
        timeout: float = 20.0,
    ) -> None:
        self.name = name
        self.command = command
        self.args = list(args or [])
        self.extra_env = dict(env or {})
        self.cwd = cwd
        self.timeout = timeout

        self._proc: subprocess.Popen | None = None
        self._pending: dict[int, dict] = {}
        self._lock = threading.Lock()
        self._next_id = 0
        self._reader: threading.Thread | None = None
        self._stderr_reader: threading.Thread | None = None
        self._stderr_tail: list[str] = []
        self._closed = False

    # ---------------------------------------------------------- 生命周期

    @property
    def alive(self) -> bool:
        # 光看 poll() 不够：进程刚退出时 poll() 可能还没更新，但读线程已经结束了。
        if self._proc is None or self._proc.poll() is not None:
            return False
        return self._reader is not None and self._reader.is_alive()

    def start(self) -> dict:
        """拉起子进程并完成 initialize 握手。返回 server 的能力声明。"""
        if self.alive:
            return {"already": True}

        env = dict(os.environ)
        env.update({str(k): str(v) for k, v in self.extra_env.items()})
        try:
            self._proc = subprocess.Popen(
                [self.command, *self.args],
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                cwd=self.cwd or None,
                env=env,
                bufsize=0,
                creationflags=_no_window(),
            )
        except FileNotFoundError as exc:
            raise MCPError(f"找不到命令 {self.command}，确认它已经安装并在 PATH 里") from exc
        except OSError as exc:
            raise MCPError(f"启动 {self.command} 失败：{exc}") from exc

        self._closed = False
        self._reader = threading.Thread(target=self._read_loop, daemon=True, name=f"mcp-{self.name}")
        self._reader.start()
        self._stderr_reader = threading.Thread(
            target=self._drain_stderr, daemon=True, name=f"mcp-{self.name}-err"
        )
        self._stderr_reader.start()

        result = self.request(
            "initialize",
            {
                "protocolVersion": PROTOCOL_VERSION,
                "capabilities": {},
                "clientInfo": CLIENT_INFO,
            },
            timeout=self.timeout,
        )
        # 握手第二步是个通知，没有响应
        self.notify("notifications/initialized")
        return result or {}

    def close(self) -> None:
        """收工。先礼（关 stdin）后兵（terminate → kill）。"""
        self._closed = True
        proc, self._proc = self._proc, None
        if proc is None:
            return

        with self._lock:  # 放掉所有等待中的请求
            for slot in self._pending.values():
                slot["box"]["error"] = MCPGone("连接已关闭")
                slot["event"].set()
            self._pending.clear()

        try:
            if proc.stdin:
                proc.stdin.close()
        except OSError:
            pass
        try:
            proc.wait(timeout=2)
        except subprocess.TimeoutExpired:
            proc.terminate()
            try:
                proc.wait(timeout=2)
            except subprocess.TimeoutExpired:
                proc.kill()
        for stream in (proc.stdout, proc.stderr):
            try:
                if stream:
                    stream.close()
            except OSError:
                pass

    # ---------------------------------------------------------- 收发

    def _take_id(self) -> int:
        with self._lock:
            self._next_id += 1
            return self._next_id

    def _write(self, message: dict) -> None:
        proc = self._proc
        if proc is None or proc.stdin is None or proc.poll() is not None:
            raise MCPGone(self._exit_hint())
        data = json.dumps(message, ensure_ascii=False).encode("utf-8") + b"\n"
        try:
            proc.stdin.write(data)
            proc.stdin.flush()
        except (BrokenPipeError, OSError) as exc:
            raise MCPGone(self._exit_hint()) from exc

    def notify(self, method: str, params: dict | None = None) -> None:
        self._write({"jsonrpc": "2.0", "method": method, "params": params or {}})

    def request(self, method: str, params: dict | None = None, timeout: float | None = None) -> Any:
        request_id = self._take_id()
        slot = {"event": threading.Event(), "box": {}}
        with self._lock:
            self._pending[request_id] = slot

        try:
            self._write(
                {"jsonrpc": "2.0", "id": request_id, "method": method, "params": params or {}}
            )
        except MCPError:
            with self._lock:
                self._pending.pop(request_id, None)
            raise

        if not slot["event"].wait(timeout if timeout is not None else self.timeout):
            with self._lock:
                self._pending.pop(request_id, None)
            raise MCPTimeout(
                f"{self.name} 超过 {timeout or self.timeout:.0f} 秒没响应（{method}）"
            )

        box = slot["box"]
        if "error" in box:
            raise box["error"]
        return box.get("result")

    # ---------------------------------------------------------- 后台线程

    def _read_loop(self) -> None:
        proc = self._proc
        if proc is None or proc.stdout is None:
            return
        try:
            for raw in proc.stdout:
                line = raw.strip()
                if not line:
                    continue
                try:
                    message = json.loads(line.decode("utf-8", "replace"))
                except json.JSONDecodeError:
                    continue  # server 偶尔会打点非协议内容，跳过
                self._dispatch(message)
        except (ValueError, OSError):
            pass
        finally:
            self._fail_pending()

    def _dispatch(self, message: dict) -> None:
        request_id = message.get("id")
        if request_id is not None and ("result" in message or "error" in message):
            with self._lock:
                slot = self._pending.pop(int(request_id), None)
            if slot is None:
                return
            error = message.get("error")
            if error:
                slot["box"]["error"] = MCPError(
                    str(error.get("message") or error)[:400]
                )
            else:
                slot["box"]["result"] = message.get("result")
            slot["event"].set()
            return

        # server 反过来请求我们（sampling / roots 这类）。我们什么都不支持，老实回错误。
        if request_id is not None and message.get("method"):
            try:
                self._write(
                    {
                        "jsonrpc": "2.0",
                        "id": request_id,
                        "error": {"code": -32601, "message": "客户端未实现该方法"},
                    }
                )
            except MCPError:
                pass

    def _fail_pending(self) -> None:
        with self._lock:
            slots = list(self._pending.values())
            self._pending.clear()
        for slot in slots:
            slot["box"]["error"] = MCPGone(self._exit_hint())
            slot["event"].set()

    def _drain_stderr(self) -> None:
        proc = self._proc
        if proc is None or proc.stderr is None:
            return
        try:
            for raw in proc.stderr:
                text = raw.decode("utf-8", "replace").rstrip()
                if text:
                    self._stderr_tail.append(text)
                    del self._stderr_tail[:-20]  # 只留最后 20 行，够诊断了
        except (ValueError, OSError):
            pass

    def _exit_hint(self) -> str:
        proc = self._proc
        code = proc.poll() if proc is not None else None
        if self._closed:
            return f"{self.name} 连接已关闭"
        tail = " / ".join(self._stderr_tail[-3:])
        hint = f"{self.name} 进程已退出" + (f"（退出码 {code}）" if code is not None else "")
        return hint + (f"：{tail}" if tail else "")

    @property
    def stderr_tail(self) -> list[str]:
        return list(self._stderr_tail)
