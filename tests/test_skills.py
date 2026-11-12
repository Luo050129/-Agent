"""技能系统测试：清单注入、按需加载、找不到时的提示。

`python tests/test_skills.py` 直接跑。
"""

from __future__ import annotations

import os
import shutil
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from miniagent import fetch, skills  # noqa: E402
from miniagent.core.prompt import system_prompt  # noqa: E402
from miniagent.skills import manager as skill_manager  # noqa: E402
from miniagent.tools.skills_tool import list_skills, use_skill  # noqa: E402

SAMPLE = """# 写周报

> 用户让你写周报时用：按结果、问题、下周计划三段

正文方法论：先列数字再写字。
"""


def test_install(tmp: Path) -> list[tuple[str, bool]]:
    """GitHub 装技能的本地与 zip 分支。SKILLS_DIR patch 到临时目录。"""
    original_dir = skill_manager.SKILLS_DIR
    skill_manager.SKILLS_DIR = tmp / "installed"
    checks: list[tuple[str, bool]] = []
    try:
        # 本地目录安装
        src = tmp / "local_skills"
        src.mkdir()
        (src / "alpha.md").write_text("# alpha\n\n> 技能 A\n", encoding="utf-8")
        (src / "beta.md").write_text("# beta\n\n> 技能 B\n", encoding="utf-8")
        keys = skill_manager.install(str(src))
        checks.append(("本地目录装进 2 个", keys == ["alpha", "beta"]))
        checks.append(("discover 能找到", "alpha" in [s.key for s in skills.discover()]))

        # 本地单文件安装
        single = tmp / "solo.md"
        single.write_text("# solo\n\n> 单文件技能\n", encoding="utf-8")
        checks.append(("单文件安装", skill_manager.install(str(single)) == ["solo"]))

        # 非法来源
        empty = tmp / "empty_dir"
        empty.mkdir()
        try:
            skill_manager.install(str(empty))
            checks.append(("空目录报错", False))
        except Exception as exc:  # noqa: BLE001
            checks.append(("空目录报错", "没有 .md" in str(exc)))
        try:
            skill_manager.install(str(tmp / "not_md.txt") + "_none")
            checks.append(("不存在的路径报错", False))
        except Exception:
            checks.append(("不存在的路径报错", True))

        # zip 分支：仓库带一层子目录，模拟 GitHub 下载
        import io
        import zipfile

        buf = io.BytesIO()
        with zipfile.ZipFile(buf, "w") as zf:
            zf.writestr("repo-main/docs/review.md", "# review\n\n> 审查方法\n")
            zf.writestr("repo-main/docs/deep/nested.md", "# nested\n\n> 嵌套方法\n")
            zf.writestr("repo-main/README.txt", "不是技能")
        payload = buf.getvalue()

        # 整仓库：只取最浅一层的 .md（review），嵌套的不收（patch 掉真实下载）
        original_download = fetch.download
        fetch.download = lambda url, timeout=30: payload  # noqa: ARG005
        try:
            keys = skill_manager.install("https://github.com/fake/repo")
        finally:
            fetch.download = original_download
        checks.append(("整仓库只收最浅层", keys == ["docs_review"]))
        checks.append(("非 md 没被装进来", "README" not in skill_manager.catalog_text()))

        # 指定子目录 + 自定义名
        fetch.download = lambda url, timeout=30: payload  # noqa: ARG005
        try:
            keys = skill_manager.install("https://github.com/fake/repo/tree/main/docs", name="my")
        finally:
            fetch.download = original_download
        checks.append(("子目录安装带名字前缀", all(k.startswith("my_") for k in keys) and len(keys) == 2))

        # 子目录里没有 md（不带 tree 的地址，让 subdir 参数生效；URL 路径优先于参数）
        fetch.download = lambda url, timeout=30: payload  # noqa: ARG005
        try:
            try:
                skill_manager.install("https://github.com/fake/repo", subdir="不存在")
                checks.append(("子目录不存在报错", False))
            except Exception:
                checks.append(("子目录不存在报错", True))
        finally:
            fetch.download = original_download

        # 装完系统提示清单会包含新技能（catalog_text 直查）
        checks.append(("清单包含已装技能", "alpha" in skill_manager.catalog_text()))
    finally:
        skill_manager.SKILLS_DIR = original_dir
    return checks


def main() -> int:
    print("[skills] 开始")
    checks: list[tuple[str, bool]] = []

    found = skills.discover()
    keys = [s.key for s in found]
    checks.append(("发现了内置技能", "code-review" in keys))
    checks.append(("标题被解析出来", any(s.name == "code-review" for s in found)))
    checks.append(("描述被解析出来", any("审代码" in s.description for s in found if s.key == "code-review")))

    target = skills.find("code-review")
    checks.append(("能按文件名找到", target is not None))
    checks.append(("能按标题找到", skills.find("code-review") is not None))

    loaded = use_skill("code-review")
    checks.append(("加载出正文", "三层" in loaded))
    checks.append(("带上了技能标识", "[技能 code-review" in loaded))

    missing = use_skill("不存在的技能")
    checks.append(("找不到时给提示", "没有这个技能" in missing and "code-review" in missing))

    listed = list_skills()
    checks.append(("列表包含内置技能", "code-review" in listed))

    prompt = system_prompt()
    checks.append(("清单进了系统提示", "可用技能" in prompt and "code-review" in prompt))
    checks.append(("提示里说明了用法", "use_skill" in prompt))

    # 临时目录里的技能也能被解析
    tmp = Path(tempfile.mkdtemp(prefix="skills_"))
    try:
        (tmp / "weekly.md").write_text(SAMPLE, encoding="utf-8")
        parsed = skills.discover(dirs=[tmp])
        checks.append(("临时目录技能被发现", len(parsed) == 1))
        checks.append(("标题取自一级标题", parsed[0].name == "写周报"))
        checks.append(("描述取自引用行", parsed[0].description.startswith("用户让你写周报")))
        checks.append(("正文可读", "先列数字" in skills.body(parsed[0])))
    finally:
        shutil.rmtree(tmp, ignore_errors=True)

    # 安装（含 GitHub zip 分支，patch 到临时目录）
    tmp2 = Path(tempfile.mkdtemp(prefix="skills_install_"))
    try:
        checks.extend(test_install(tmp2))
    finally:
        shutil.rmtree(tmp2, ignore_errors=True)

    for label, ok in checks:
        print(f"  {'PASS' if ok else 'FAIL'}  {label}")
    ok_all = all(ok for _, ok in checks)
    print(f"[skills] {'全部通过' if ok_all else '存在失败项'}")
    return 0 if ok_all else 1


if __name__ == "__main__":
    raise SystemExit(main())
