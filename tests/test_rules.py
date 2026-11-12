"""边界测试：agent.md 规则注入 + 读写白名单 + 命令黑名单。

`python tests/test_rules.py` 直接跑。
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from miniagent.config import BASE_DIR  # noqa: E402
from miniagent.core.prompt import system_prompt  # noqa: E402
from miniagent.core.rules import Policy, agent_rules, inside  # noqa: E402


def main() -> int:
    print("[rules] 开始")
    checks: list[tuple[str, bool]] = []
    policy = Policy.default()

    checks.append(("项目内路径放行", inside(BASE_DIR / "agent.py", [BASE_DIR])))
    checks.append(("项目外路径拒绝", not inside("C:/Windows/System32/drivers/etc/hosts", [BASE_DIR])))

    deny = policy.check("write_file", {"path": str(BASE_DIR / "tmp_probe.txt"), "content": "x"})
    checks.append(("项目内写入放行", deny is None))

    deny = policy.check("write_file", {"path": "C:/Windows/tmp_probe.txt", "content": "x"})
    checks.append(("项目外写入被拦", deny is not None and "写入范围" in deny))

    deny = policy.check("read_file", {"path": "C:/Windows/win.ini"})
    checks.append(("项目外读取被拦", deny is not None and "读取范围" in deny))

    deny = policy.check("read_document", {"path": "C:/Windows/win.ini"})
    checks.append(("文档读取越界被拦", deny is not None and "读取范围" in deny))

    # 用户主动附加的文件才放行，且只放开这一个路径
    import tempfile

    tmpdir = tempfile.mkdtemp(prefix="policy_")
    probe = Path(tmpdir) / "attach.md"
    probe.write_text("# 用户附件", encoding="utf-8")
    checks.append(("未附加时被拦", policy.check("read_document", {"path": str(probe)}) is not None))
    policy.allow_read(probe)
    checks.append(("附加后同一文件放行", policy.check("read_document", {"path": str(probe)}) is None))
    checks.append(
        ("同目录其他文件仍被拦",
         policy.check("read_document", {"path": str(Path(tmpdir) / "other.md")}) is not None)
    )
    import shutil as _shutil

    _shutil.rmtree(tmpdir, ignore_errors=True)

    checks.append(("普通命令放行", policy.check("run_shell", {"command": "echo hi"}) is None))
    checks.append(
        ("格式化命令被拦", policy.check("run_shell", {"command": "format c: /q"}) is not None)
    )
    checks.append(("关机命令被拦", policy.check("run_shell", {"command": "shutdown /s"}) is not None))
    checks.append(
        ("递归删根被拦", policy.check("run_shell", {"command": "rm -rf /"}) is not None)
    )
    checks.append(
        ("代码删根被拦",
         policy.check("run_python", {"code": "import shutil; shutil.rmtree('/')"}) is not None)
    )
    checks.append(
        ("重定向越界被拦",
         policy.check("run_shell", {"command": "echo hi > C:\\Windows\\p0.txt"}) is not None)
    )
    checks.append(
        ("项目内重定向放行",
         policy.check("run_shell", {"command": f"echo hi > {BASE_DIR / 'probe.txt'}"}) is None)
    )
    checks.append(
        ("相对路径重定向放行",
         policy.check("run_shell", {"command": "echo hi > probe.txt"}) is None)
    )

    rules_text = agent_rules()
    checks.append(("读到了运行时规则", bool(rules_text) and "能做什么" in rules_text))
    checks.append(
        ("不注入 agent.md 的开发约定", "不引入第三方依赖" not in rules_text)
    )

    prompt = system_prompt()
    checks.append(("系统提示里带了项目规则", "项目规则" in prompt))
    checks.append(("规则内容真的注入了", "回答风格" in prompt))

    custom = system_prompt(extra_rules="只许只读操作")
    checks.append(("可以覆盖规则内容", "只许只读操作" in custom and "回答风格" not in custom))

    # 预算：整段取舍，不切半句
    from miniagent.core.context import estimate_text

    full = agent_rules(budget_tokens=100000)
    tiny = agent_rules(budget_tokens=200)
    checks.append(("预算裁剪确实生效", estimate_text(tiny) < estimate_text(full)))
    checks.append(("小预算也不返回空", bool(tiny.strip())))
    checks.append(("小预算不超太多", estimate_text(tiny) <= 260))
    checks.append(("裁剪按整节，不留半截标题", tiny.startswith("#")))
    checks.append(("裁剪会说明还剩几节", "未注入" in agent_rules(budget_tokens=200)))
    checks.append(("预算够时不加省略提示", "未注入" not in full))

    # 缓存：文件改了要失效，不能一直读旧的
    import tempfile
    import time

    tmpdir = Path(tempfile.mkdtemp())
    probe = tmpdir / "rules.md"
    probe.write_text("## 甲节\n内容 AAA", encoding="utf-8")
    os.environ["AGENT_RULES_FILE"] = str(probe)
    first = agent_rules()
    time.sleep(0.02)
    probe.write_text("## 乙节\n内容 BBB", encoding="utf-8")
    second = agent_rules()
    os.environ.pop("AGENT_RULES_FILE", None)
    checks.append(("能读到指定路径的规则", "内容 AAA" in first))
    checks.append(("文件改了缓存会失效", "内容 BBB" in second and "内容 AAA" not in second))

    for label, ok in checks:
        print(f"  {'PASS' if ok else 'FAIL'}  {label}")
    ok_all = all(ok for _, ok in checks)
    print(f"[rules] {'全部通过' if ok_all else '存在失败项'}")
    return 0 if ok_all else 1


if __name__ == "__main__":
    raise SystemExit(main())
