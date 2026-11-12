"""沙箱测试：进程树隔离、Python 子进程守卫、shell 读目标检查、开关降级。

`python tests/test_sandbox.py` 直接跑。所有越界目标都指向
C:/Windows/win.ini（真实存在但绝不许读）和 C:/ 盘根的探针路径。
"""

from __future__ import annotations

import os
import sys
import tempfile
import time
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from miniagent.config import BASE_DIR  # noqa: E402
from miniagent.core.rules import Policy  # noqa: E402
from miniagent.tools import runtime, sandbox  # noqa: E402

NOPE = "C:/Windows/win.ini"  # 越界读的固定靶子


def _enable_default_roots() -> None:
    policy = Policy.default()
    sandbox.set_roots(policy.read_roots, policy.write_roots)


def test_normal_execution() -> list[tuple[str, bool]]:
    """沙箱开着，正常用法必须全部照旧。"""
    _enable_default_roots()
    checks = [
        ("能算数", "2" in runtime.run_python("print(1 + 1)")),
        ("能读工作区文件", "墟" in runtime.run_python("print(open('README.md', encoding='utf-8').readline())")),
        ("能写工作区文件", "written" in runtime.run_python(
            "open('.agent/_sb_probe.txt', 'w').write('ok'); print('written')")),
        ("pathlib 正常", "墟" in runtime.run_python(
            "from pathlib import Path; print(Path('README.md').read_text(encoding='utf-8')[:20])")),
        ("stdlib 可用", '{"a": 1}' in runtime.run_python("import json; print(json.dumps({'a': 1}))")),
        ("tempfile 可用", "ok" in runtime.run_python(
            "import tempfile, os; p = os.path.join(tempfile.gettempdir(), '_sb_t.txt');"
            " open(p, 'w').write('ok'); print(open(p).read())")),
        ("shell 正常", "hello" in runtime.run_shell("echo hello")),
        ("工作区写入真的落了盘", Path(BASE_DIR / ".agent" / "_sb_probe.txt").is_file()),
        ("守卫源码能编译", compile(sandbox.wrap_python_source("pass"), "<g>", "exec") is not None),
    ]
    Path(BASE_DIR / ".agent" / "_sb_probe.txt").unlink(missing_ok=True)
    return checks


def test_fs_guard() -> list[tuple[str, bool]]:
    _enable_default_roots()
    outside_read = runtime.run_python(f"open({NOPE!r}).read()")
    outside_write = runtime.run_python("open('C:/_sb_escape.txt', 'w').write('x')")
    outside_pathlib = runtime.run_python(f"from pathlib import Path; Path({NOPE!r}).read_text()")
    outside_unlink = runtime.run_python(f"import os; os.remove({NOPE!r})")
    outside_rmtree = runtime.run_python("import shutil; shutil.rmtree('C:/Windows/Temp')")
    return [
        ("越界读被拦", "[SANDBOX]" in outside_read and "exit_code=3" in outside_read),
        ("结果带 [ERR] 标记", outside_read.startswith("[ERR]")),
        ("越界写被拦", "[SANDBOX]" in outside_write and "写入" in outside_write),
        ("pathlib 越界读被拦", "[SANDBOX]" in outside_pathlib),
        ("os.remove 越界被拦", "[SANDBOX]" in outside_unlink),
        ("shutil.rmtree 越界被拦", "[SANDBOX]" in outside_rmtree),
        ("盘根探针没被写出来", not Path("C:/_sb_escape.txt").exists()),
        ("拦截信息带允许范围", "允许读" in outside_read),
    ]


def test_process_and_net_guard() -> list[tuple[str, bool]]:
    _enable_default_roots()
    spawn = runtime.run_python("import subprocess; subprocess.run('echo hi')")
    system = runtime.run_python("import os; os.system('echo hi')")
    net = runtime.run_python("import socket; socket.socket()")
    return [
        ("subprocess 被拦", "[SANDBOX]" in spawn and "子进程" in spawn),
        ("os.system 被拦", "[SANDBOX]" in system),
        ("socket 被拦", "[SANDBOX]" in net and "网络" in net),
    ]


def test_timeout_kills_tree() -> list[tuple[str, bool]]:
    _enable_default_roots()
    start = time.monotonic()
    result = runtime.run_shell("ping -n 30 127.0.0.1", timeout=2)
    elapsed = time.monotonic() - start
    return [
        ("超时返回错误", "[ERR]" in result and "超时" in result),
        ("超时后及时收手（没有干等 30 秒）", elapsed < 10),
        ("报告进程树已终止", "进程树已终止" in result),
    ]


def test_shell_read_targets() -> list[tuple[str, bool]]:
    policy = Policy.default()
    sandbox.set_roots(policy.read_roots, policy.write_roots)
    denied = policy.check("run_shell", {"command": f"type {NOPE}"})
    backslash = policy.check("run_shell", {"command": r"type C:\\Windows\\win.ini"})
    quoted = policy.check("run_shell", {"command": f"get-content \"{NOPE}\""})
    inside = policy.check("run_shell", {"command": "type README.md"})
    relative = policy.check("run_shell", {"command": "cat 某个相对路径.txt"})
    pipe = policy.check("run_shell", {"command": "echo hi | more"})
    setx = policy.check("run_shell", {"command": "setx PATH hello"})
    return [
        ("type 绝对路径越界被拦", denied is not None and "读取白名单" in denied),
        ("反斜杠路径同样被拦", backslash is not None),
        ("get-content 带引号被拦", quoted is not None),
        ("工作区内文件放行", inside is None),
        ("相对路径不检查（没信息可判）", relative is None),
        ("管道分页 | more 不误伤", pipe is None),
        ("setx 持久化写环境变量被拦", setx is not None and "环境变量" in setx),
    ]


def test_disabled_fallback() -> list[tuple[str, bool]]:
    """AGENT_SANDBOX=0：不注入守卫、不传白名单，老行为原样保留。"""
    os.environ["AGENT_SANDBOX"] = "0"
    try:
        _enable_default_roots()  # 开关关着，set_roots 应为空操作
        result = runtime.run_python(f"print(open({NOPE!r}).readline().strip()[:10])")
        live = runtime.run_python("print('alive')")
        checks = [
            ("关闭后越界读放行（老行为）", "[SANDBOX]" not in result and "exit_code=0" in result),
            ("关闭后正常执行照旧", "alive" in live),
        ]
    finally:
        os.environ.pop("AGENT_SANDBOX", None)
    return checks


def test_roots_follow_policy() -> list[tuple[str, bool]]:
    """内核每次执行前同步白名单：allow_read 加进来的路径守卫要认。"""
    policy = Policy.default()
    sandbox.set_roots(policy.read_roots, policy.write_roots)
    blocked = runtime.run_python(f"open({NOPE!r}).read()")
    policy.allow_read(NOPE)
    sandbox.set_roots(policy.read_roots, policy.write_roots)
    allowed = runtime.run_python(f"print(open({NOPE!r}, encoding='utf-8', errors='replace').readline().strip()[:5])")
    return [
        ("同步前越界被拦", "[SANDBOX]" in blocked),
        ("allow_read 加白后放行", "[SANDBOX]" not in allowed and "exit_code=0" in allowed),
    ]


def main() -> int:
    print("[sandbox] 开始")
    checks = (
        test_normal_execution()
        + test_fs_guard()
        + test_process_and_net_guard()
        + test_timeout_kills_tree()
        + test_shell_read_targets()
        + test_disabled_fallback()
        + test_roots_follow_policy()
    )
    for label, ok in checks:
        print(f"  {'PASS' if ok else 'FAIL'}  {label}")
    ok_all = all(ok for _, ok in checks)
    print(f"[sandbox] {'全部通过' if ok_all else '存在失败项'}")
    return 0 if ok_all else 1


if __name__ == "__main__":
    raise SystemExit(main())
