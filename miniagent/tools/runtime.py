"""工具层：执行命令和跑代码。

两条执行通道都走进程级沙箱（tools/sandbox.py）：
    run_shell   Job Object 进程树隔离，超时杀全树
    run_python  Job Object + 子进程内守卫（文件读写按白名单拦截，禁子进程/网络）
"""

from __future__ import annotations

import os
import subprocess
import sys

from . import sandbox
from ._util import ERR, truncate


def _format(returncode: int, out: str, err: str) -> str:
    """统一输出：exit_code 打头，非零就是失败，别让下游猜 stderr。"""
    parts = [f"{ERR if returncode != 0 else ''}exit_code={returncode}"]
    if out:
        parts.append(f"stdout:\n{out.strip()}")
    if err:
        parts.append(f"stderr:\n{err.strip()}")
    if not out.strip() and not err.strip():
        parts.append("（无输出）")
    return truncate("\n".join(parts))


def run_shell(command: str, cwd: str | None = None, timeout: int = 60) -> str:
    """执行 shell 命令。Windows 下走 cmd.exe，进程树在 Job Object 里。"""
    try:
        proc, job = sandbox.popen_guarded(
            command,
            shell=True,
            cwd=cwd or os.getcwd(),
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            encoding="utf-8",
            errors="replace",
        )
    except OSError as exc:
        return f"{ERR}命令启动失败：{exc}"

    returncode, out, err, timed_out = sandbox.collect(proc, job, timeout)
    if timed_out:
        return f"{ERR}命令超时（>{timeout}s，进程树已终止）：{command}"
    return _format(returncode, out, err)


def run_python(code: str, timeout: int = 60) -> str:
    """在沙箱化的独立进程里跑一段 Python，做计算或数据处理。"""
    try:
        args, env, script = sandbox.run_python_args(code)
    except OSError as exc:
        return f"{ERR}无法准备执行环境：{exc}"

    try:
        try:
            proc, job = sandbox.popen_guarded(
                args,
                cwd=os.getcwd(),
                env=env,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                encoding="utf-8",
                errors="replace",
            )
        except OSError as exc:
            return f"{ERR}Python 执行失败：{exc}"

        returncode, out, err, timed_out = sandbox.collect(proc, job, timeout)
        if timed_out:
            return f"{ERR}Python 执行超时（>{timeout}s，进程树已终止）"
        return _format(returncode, out, err)
    finally:
        try:
            os.remove(script)
        except OSError:
            pass


SCHEMAS = [
    {
        "type": "function",
        "function": {
            "name": "run_shell",
            "description": "执行一条 shell 命令（Windows 为 cmd.exe），用于查看系统信息、运行程序、git 操作。返回 exit_code 与 stdout/stderr。命令受边界白名单与沙箱约束。",
            "parameters": {
                "type": "object",
                "properties": {
                    "command": {"type": "string", "description": "要执行的命令"},
                    "cwd": {"type": "string", "description": "工作目录，默认当前目录"},
                    "timeout": {"type": "integer", "description": "超时秒数，默认 60"},
                },
                "required": ["command"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "run_python",
            "description": "在沙箱化的独立进程执行一段 Python 代码，适合精确计算、数据处理、验证算法。文件读写限制在允许目录内，禁止再起子进程和联网。print 的内容会返回。",
            "parameters": {
                "type": "object",
                "properties": {
                    "code": {"type": "string", "description": "完整的 Python 代码"},
                    "timeout": {"type": "integer", "description": "超时秒数，默认 60"},
                },
                "required": ["code"],
            },
        },
    },
]

HANDLERS = {"run_shell": run_shell, "run_python": run_python}
DANGEROUS = {"run_shell", "run_python"}
