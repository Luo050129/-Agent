"""工具层：进程级沙箱。

边界白名单（core/rules.py）管的是「工具参数里的路径」，但 run_python 的代码
在子进程里跑，`open("C:/任何路径")` 直接绕过——这是本项目最大的诚实留白。
这一层补三件事：

  1. Job Object 进程树隔离（ctypes 调 kernel32，零依赖、无需管理员）
     kill-on-close + 内存上限：超时不再只杀直接子进程，孙进程一起死
  2. Python 子进程守卫：给 run_python 的子进程注入守卫源码，patch
     open / os / shutil / subprocess / socket，按读/写白名单拦截。
     拦得住常规绕过（含 pathlib，它底层走 os 和 io.open），拦不住
     C 扩展级逃逸（ctypes 写内存）——这个如实写进文档，不假装解决
  3. 白名单同步：内核每次执行工具前调 set_roots()，把 Policy 的
     读/写白名单递给子进程（走环境变量）

降级策略：AGENT_SANDBOX=0 全关；非 Windows 平台只关 Job Object（守卫源码
是纯 Python，跨平台）；Job 创建失败静默退化为普通 Popen。
"""

from __future__ import annotations

import os
import signal
import subprocess
import sys
import tempfile
from pathlib import Path

from ..config import BASE_DIR, sandbox_enabled, sandbox_mem_mb

# 白名单在子进程里通过环境变量读取
ENV_READ_ROOTS = "AGENT_SANDBOX_READ_ROOTS"
ENV_WRITE_ROOTS = "AGENT_SANDBOX_WRITE_ROOTS"

_state: dict = {"read": None, "write": None}  # None = 未同步过，用默认值


def set_roots(read_roots, write_roots) -> None:
    """内核在每次执行工具前同步 Policy 的白名单。沙箱关闭时是空操作。"""
    if not sandbox_enabled():
        return
    _state["read"] = [str(r) for r in read_roots or []]
    _state["write"] = [str(r) for r in write_roots or []]


def _roots() -> tuple[list[str], list[str]]:
    read = _state["read"]
    write = _state["write"]
    if read is None or write is None:  # 从没同步过（直跑测试），用默认边界
        default = [str(BASE_DIR), os.getcwd()]
        read = read or default
        write = write or [str(BASE_DIR)]
    return read, write


def _env() -> dict | None:
    """给子进程带上白名单环境变量。None = 沿用父进程环境。"""
    if not sandbox_enabled():
        return None
    read, write = _roots()
    env = dict(os.environ)
    env[ENV_READ_ROOTS] = os.pathsep.join(read)
    env[ENV_WRITE_ROOTS] = os.pathsep.join(write)
    return env


# ================================================================ Job Object

_IS_WIN = sys.platform == "win32"
if _IS_WIN:
    import ctypes
    from ctypes import wintypes

    _kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)

    _JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE = 0x00002000
    _JOB_OBJECT_LIMIT_PROCESS_MEMORY = 0x00000100
    _JobObjectExtendedLimitInformation = 9

    class _IO_COUNTERS(ctypes.Structure):
        _fields_ = [(name, ctypes.c_ulonglong) for name in (
            "ReadOperationCount", "WriteOperationCount", "OtherOperationCount",
            "ReadTransferCount", "WriteTransferCount", "OtherTransferCount",
        )]

    class _BASIC_LIMIT_INFO(ctypes.Structure):
        _fields_ = [
            ("PerProcessUserTimeLimit", ctypes.c_longlong),
            ("PerJobUserTimeLimit", ctypes.c_longlong),
            ("LimitFlags", wintypes.DWORD),
            ("MinimumWorkingSetSize", ctypes.c_size_t),
            ("MaximumWorkingSetSize", ctypes.c_size_t),
            ("ActiveProcessLimit", wintypes.DWORD),
            ("Affinity", ctypes.c_size_t),
            ("PriorityClass", wintypes.DWORD),
            ("SchedulingClass", wintypes.DWORD),
        ]

    class _EXTENDED_LIMIT_INFO(ctypes.Structure):
        _fields_ = [
            ("BasicLimitInformation", _BASIC_LIMIT_INFO),
            ("IoInfo", _IO_COUNTERS),
            ("ProcessMemoryLimit", ctypes.c_size_t),
            ("JobMemoryLimit", ctypes.c_size_t),
            ("PeakProcessMemoryUsed", ctypes.c_size_t),
            ("PeakJobMemoryUsed", ctypes.c_size_t),
        ]


def _make_job() -> int | None:
    """建一个 Job Object：kill-on-close + 内存上限。失败返回 None（降级）。"""
    if not _IS_WIN:
        return None
    try:
        job = _kernel32.CreateJobObjectW(None, None)
        if not job:
            return None
        info = _EXTENDED_LIMIT_INFO()
        info.BasicLimitInformation.LimitFlags = _JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE
        mem_mb = sandbox_mem_mb()
        if mem_mb > 0:
            info.BasicLimitInformation.LimitFlags |= _JOB_OBJECT_LIMIT_PROCESS_MEMORY
            info.ProcessMemoryLimit = mem_mb * 1024 * 1024
        if not _kernel32.SetInformationJobObject(job, _JobObjectExtendedLimitInformation,
                                                 ctypes.byref(info), ctypes.sizeof(info)):
            _kernel32.CloseHandle(job)
            return None
        return job
    except Exception:  # noqa: BLE001 沙箱基础设施失败不该拖垮工具执行
        return None


def _assign(job: int, proc: subprocess.Popen) -> None:
    try:
        _kernel32.AssignProcessToJobObject(job, int(proc._handle))  # noqa: SLF001
    except Exception:  # noqa: BLE001
        pass  # 分配失败就当没有 Job，普通执行


def popen_guarded(args, **kwargs) -> tuple[subprocess.Popen, int | None]:
    """带进程树隔离的 Popen。返回 (proc, job_handle)。job_handle 为 None 表示无隔离。

    非 Windows 没有 Job Object，退化为 POSIX 进程组（start_new_session），
    kill_tree 时 killpg 整组杀——否则孙进程占着输出管道，communicate 会卡满超时。
    """
    if not _IS_WIN:
        kwargs.setdefault("start_new_session", True)
    proc = subprocess.Popen(args, **kwargs)
    job = _make_job()
    if job:
        _assign(job, proc)
    return proc, job


def collect(proc: subprocess.Popen, job: int | None, timeout: int) -> tuple[int, str, str, bool]:
    """收输出。超时杀整棵进程树。返回 (returncode, stdout, stderr, timed_out)。"""
    try:
        out, err = proc.communicate(timeout=timeout)
        rc, timed_out = proc.returncode, False
    except subprocess.TimeoutExpired:
        _kill_tree(proc, job)
        out, err = proc.communicate()
        rc, timed_out = proc.returncode, True
    finally:
        if job:  # kill-on-close：句柄一关，残留的孙进程也一起死
            _kernel32.CloseHandle(job)
    return rc, (out or ""), (err or ""), timed_out


def _kill_tree(proc: subprocess.Popen, job: int | None) -> None:
    if job:
        try:
            _kernel32.TerminateJobObject(job, 1)
        except Exception:  # noqa: BLE001
            pass
    elif hasattr(os, "killpg") and hasattr(os, "setsid"):
        try:
            os.killpg(os.getpgid(proc.pid), signal.SIGKILL)
        except (ProcessLookupError, PermissionError, OSError):
            pass
    proc.kill()
    try:
        proc.wait(timeout=5)
    except subprocess.TimeoutExpired:
        pass


# ================================================================ Python 子进程守卫

# 纯静态源码：白名单走环境变量，用户代码由父进程拼在文件末尾。
# patch 顺序有讲究：先把要用的模块 import 完，再打补丁——否则连 import 都会过不了自己。
_GUARD_SRC = '''
# --- 墟沙箱守卫（自动注入，勿改） ---
import builtins, io, os, re, sys, tempfile, shutil, subprocess, socket

def _roots(name):
    return [p for p in os.environ.get(name, "").split(os.pathsep) if p]

_READ = _roots("AGENT_SANDBOX_READ_ROOTS")
_WRITE = _roots("AGENT_SANDBOX_WRITE_ROOTS")
# Python 自身安装目录和系统临时目录天然放行：否则 import json / tempfile 都过不了
_READ += [sys.prefix, sys.exec_prefix, tempfile.gettempdir()]
_WRITE += [tempfile.gettempdir()]

_ON_WIN = sys.platform == "win32"
# 非 Windows 平台没有盘符语义：C:/Windows/x.txt 会被解析成 cwd 下的相对路径，
# 恰好落进白名单。模型给盘符路径大概率是想越界，一律拒绝。
_DRIVE = re.compile(r"^[A-Za-z]:[/\\\\]|^\\\\\\\\|^//")

def _norm(p):
    try:
        return os.path.normcase(os.path.realpath(str(p)))
    except Exception:
        return os.path.normcase(os.path.abspath(str(p)))

def _inside(p, roots):
    if not _ON_WIN and _DRIVE.match(str(p)):
        return False
    n = _norm(p)
    for r in roots:
        rn = _norm(r)
        if n == rn or n.startswith(rn + os.sep):
            return True
    return False

def _deny(action, p):
    return PermissionError(
        f"{action}被沙箱拦截：{p}\\n（允许读：{'; '.join(_READ) or '无'}）"
    )

def _path_of(file):
    try:
        return os.fspath(file)
    except TypeError:
        return None

# ---- open：按模式判定读/写 ----
_real_open = builtins.open
def _open(file, mode="r", *a, **k):
    path = _path_of(file)
    if path is None:  # 数字文件描述符：来自已检查的打开，放行
        return _real_open(file, mode, *a, **k)
    needs_write = any(ch in (mode or "r") for ch in "wax+")
    ok = _inside(path, _WRITE if needs_write else _READ)
    if not ok:
        raise _deny("写入" if needs_write else "读取", path)
    return _real_open(file, mode, *a, **k)
builtins.open = _open
io.open = _open

# ---- os：写操作查写白名单，读操作查读白名单 ----
def _wrap_os1(name, roots, action):
    real = getattr(os, name)
    def wrapped(path, *a, **k):
        if not _inside(path, roots):
            raise _deny(action, path)
        return real(path, *a, **k)
    return wrapped

for _name in ("remove", "unlink", "mkdir", "makedirs", "rmdir", "removedirs",
              "truncate", "chmod", "link", "symlink"):
    if hasattr(os, _name):
        setattr(os, _name, _wrap_os1(_name, _WRITE, "写入/修改"))

def _os_rename(old, new, *a, **k):
    if not (_inside(old, _WRITE) and _inside(new, _WRITE)):
        raise _deny("写入/修改", f"{old} -> {new}")
    return _real_rename(old, new, *a, **k)
_real_rename = os.rename
os.rename = _os_rename
if hasattr(os, "replace"):
    os.replace = _os_rename

for _name in ("listdir", "scandir", "walk"):
    if hasattr(os, _name):
        setattr(os, _name, _wrap_os1(_name, _READ, "读取"))

# ---- shutil：树级删除/复制/移动 ----
_real_rmtree = shutil.rmtree
def _rmtree(path, *a, **k):
    if not _inside(path, _WRITE):
        raise _deny("写入/修改", path)
    return _real_rmtree(path, *a, **k)
shutil.rmtree = _rmtree

_real_copy = shutil.copy
def _copy(src, dst, *a, **k):
    if not (_inside(src, _READ) and _inside(dst, _WRITE)):
        raise _deny("复制", f"{src} -> {dst}")
    return _real_copy(src, dst, *a, **k)
shutil.copy = _copy
shutil.copy2 = _copy

_real_move = shutil.move
def _move(src, dst, *a, **k):
    if not (_inside(src, _WRITE) and _inside(dst, _WRITE)):
        raise _deny("移动", f"{src} -> {dst}")
    return _real_move(src, dst, *a, **k)
shutil.move = _move

# ---- 进程与网络：沙箱内一律禁止（要跑命令用 run_shell，要上网用 web_read） ----
def _no_exec(*a, **k):
    raise PermissionError("沙箱内禁止再启动子进程（os.system / subprocess）")

os.system = _no_exec
os.startfile = _no_exec
for _name in list(dir(os)):
    if _name.startswith(("spawn", "exec", "popen")):
        setattr(os, _name, _no_exec)
subprocess.Popen = _no_exec
subprocess.run = _no_exec
subprocess.call = _no_exec
subprocess.check_output = _no_exec
subprocess.check_call = _no_exec

if os.environ.get("AGENT_SANDBOX_NET", "") != "1":
    def _no_conn(*a, **k):
        raise PermissionError("沙箱内禁止网络连接（socket）")
    class _NoSocket:
        def __init__(self, *a, **k):
            raise _no_conn()
    socket.socket = _NoSocket
    socket.create_connection = _no_conn

def _run_user(src):
    try:
        exec(compile(src, "<agent-code>", "exec"), {"__name__": "__main__"})
    except PermissionError as exc:
        sys.stderr.write(f"[SANDBOX] {exc}\\n")
        raise SystemExit(3) from None
# --- 守卫结束 ---
'''


def wrap_python_source(code: str) -> str:
    """守卫源码 + 用户代码，拼成一个可执行脚本。"""
    return _GUARD_SRC + "\n\n_run_user(" + repr(code) + ")\n"


def run_python_args(code: str) -> tuple[list[str], dict | None, str]:
    """准备 run_python 的子进程：临时脚本 + 白名单环境变量。

    返回 (命令行, 环境变量或 None, 临时脚本路径)。脚本由调用方负责清理。
    走临时文件而不是 -c：Windows 命令行上限 32k 字符，长代码会静默截断。
    沙箱关闭时不注入守卫——否则缺了白名单环境变量，守卫会把一切路径都拦死。
    """
    fd, script = tempfile.mkstemp(prefix="agent_pyexec_", suffix=".py", text=True)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            fh.write(wrap_python_source(code) if sandbox_enabled() else code)
    except OSError:
        Path(script).unlink(missing_ok=True)
        raise
    return [sys.executable, script], _env(), script


def is_windows() -> bool:
    return _IS_WIN
