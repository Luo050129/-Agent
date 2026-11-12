"""技能层：`skills/*.md` 是按需加载的知识。

插件和技能的区别一句话说清：
    插件给 Agent 加「能做什么」（工具）
    技能给 Agent 加「该怎么做」（方法、清单、口径）

清单（名字 + 一句描述）常驻系统提示词，很省 token；正文要模型主动调 `use_skill` 才加载，
避免把一堆方法论塞满上下文。

一个技能文件长这样：

    # code-review

    > 审查代码时用：按结构、风险、可读性三层给意见

    正文：具体怎么做……

技能支持从 GitHub / 本地安装（install）：技能是纯 Markdown，没有代码执行风险，
这点和插件不同，确认话术也不一样。
"""

from __future__ import annotations

import os
import re
import shutil
from dataclasses import dataclass
from pathlib import Path

from .. import fetch
from ..config import BASE_DIR

SKILLS_DIR = BASE_DIR / "skills"
DESC_MAX = 60
BODY_MAX = 8000

# 技能文件名：挡路径分隔符和 Windows 非法字符，其余（含中文）放行
_UNSAFE_NAME = re.compile(r'[\\/:*?"<>|]+')


@dataclass
class Skill:
    key: str  # 文件名，工具调用用这个
    name: str  # 标题
    description: str
    path: Path


def skill_dirs() -> list[Path]:
    """默认 skills/，可用 AGENT_SKILLS_DIR 追加（分号分隔）。"""
    dirs = [SKILLS_DIR]
    extra = os.getenv("AGENT_SKILLS_DIR", "").strip()
    if extra:
        dirs.extend(Path(p).expanduser() for p in extra.split(";") if p.strip())
    return [d for d in dirs if d.is_dir()]


def _parse(path: Path) -> Skill:
    text = path.read_text(encoding="utf-8", errors="replace")
    lines = text.splitlines()

    name = path.stem
    description = ""
    for line in lines:
        stripped = line.strip()
        if stripped.startswith("# "):
            name = stripped[2:].strip()
            continue
        if stripped.startswith(">"):
            description = stripped.lstrip("> ").strip()
            break
        if stripped and not stripped.startswith("#"):
            description = stripped
            break

    if len(description) > DESC_MAX:
        description = description[:DESC_MAX] + "…"
    return Skill(key=path.stem, name=name, description=description or "（无描述）", path=path)


def discover(dirs: list[Path] | None = None) -> list[Skill]:
    skills: list[Skill] = []
    seen: set[str] = set()
    for directory in dirs if dirs is not None else skill_dirs():
        for path in sorted(directory.glob("*.md")):
            if path.stem in seen:
                continue
            try:
                skill = _parse(path)
            except OSError:
                continue
            seen.add(skill.key)
            skills.append(skill)
    return skills


def body(skill: Skill) -> str:
    text = skill.path.read_text(encoding="utf-8", errors="replace").strip()
    if len(text) > BODY_MAX:
        text = text[:BODY_MAX] + f"\n…（技能过长已截断，原 {len(text)} 字符）"
    return text


def find(name: str, dirs: list[Path] | None = None) -> Skill | None:
    target = (name or "").strip().lower()
    if not target:
        return None
    for skill in discover(dirs):
        if target in (skill.key.lower(), skill.name.lower()):
            return skill
    return None


def catalog_text(dirs: list[Path] | None = None) -> str:
    """给系统提示词用的清单，没有技能就返回空串。"""
    skills = discover(dirs)
    if not skills:
        return ""
    lines = ["可用技能（下面的目录里有完整方法；需要时先用 use_skill 加载正文，再照着做）："]
    lines.extend(f"- {s.key}：{s.description}" for s in skills)
    return "\n".join(lines)


# ---------------------------------------------------------------- 安装


def _safe_name(name: str) -> str:
    cleaned = _UNSAFE_NAME.sub("_", (name or "").strip()).strip("._")
    if not cleaned:
        raise fetch.FetchError("技能名不合法")
    return cleaned[:80]


def _write_skill(filename: str, data: bytes) -> str:
    key = _safe_name(Path(filename).stem)
    SKILLS_DIR.mkdir(parents=True, exist_ok=True)
    (SKILLS_DIR / f"{key}.md").write_bytes(data)
    return key


def install(source: str, name: str | None = None, subdir: str | None = None) -> list[str]:
    """从 GitHub 地址或本地路径安装技能（Markdown 文件）。

    支持四种来源：
        https://github.com/owner/repo                          整个仓库（找 .md）
        https://github.com/owner/repo/tree/main/sub            仓库子目录
        https://github.com/owner/repo/blob/main/skill.md       单个 .md 文件
        本地目录或单个 .md 文件                                  离线安装

    返回装进的技能 key 列表；一个 .md 都没有就报错。同名技能直接覆盖。
    """
    source = (source or "").strip()
    if not source:
        raise fetch.FetchError("地址不能为空")

    local = Path(source).expanduser()
    if local.is_file():
        if local.suffix.lower() != ".md":
            raise fetch.FetchError(f"技能只能是 Markdown 文件：{local.name}")
        return [_write_skill(name or local.name, local.read_bytes())]

    if local.is_dir():
        keys = [
            _write_skill(md.name, md.read_bytes())
            for md in sorted(local.glob("*.md"))
            if md.is_file()
        ]
        if not keys:
            raise fetch.FetchError(f"目录里没有 .md 技能文件：{source}")
        return keys

    if "github.com" in source:
        return _install_from_github(source, name, subdir)

    raise fetch.FetchError("只支持 GitHub 地址、本地目录或 .md 文件")


def _install_from_github(source: str, name: str | None, subdir: str | None) -> list[str]:
    parts = source.rstrip("/").split("github.com/", 1)[-1].split("/")
    if len(parts) < 2:
        raise fetch.FetchError("GitHub 地址格式不对，应该形如 https://github.com/owner/repo")

    repo = f"{parts[0]}/{parts[1]}"
    branch = None
    if len(parts) >= 4 and parts[2] in ("tree", "blob"):
        branch = parts[3]
        if parts[2] == "blob":
            # 单文件：必须是 .md
            path = "/".join(parts[4:])
            if not path.lower().endswith(".md"):
                raise fetch.FetchError(f"技能只能是 Markdown 文件：{path}")
            raw = f"https://raw.githubusercontent.com/{repo}/{branch}/{path}"
            key = _write_skill(name or Path(path).name, fetch.download(raw))
            return [key]
        if parts[2] == "tree" and len(parts) >= 5:
            subdir = "/".join(parts[4:])

    payload, ref = fetch.github_zip(repo, branch)
    names = fetch.zip_list(payload)
    mds = [n for n in names if n.lower().endswith(".md") and not n.endswith("/")]

    if subdir:
        # GitHub zip 成员带 "repo-main/" 顶层目录，所以按「/子目录/」段匹配，不按开头
        tail = subdir.rstrip("/") + "/"
        picked = []
        for n in mds:
            idx = n.find(f"/{tail}")
            start = idx + 1 + len(tail) if idx != -1 else (len(tail) if n.startswith(tail) else -1)
            if start != -1:
                picked.append((n, n[start:]))
        if not picked:
            raise fetch.FetchError(f"压缩包里没有找到 {subdir}/ 下的 .md 技能文件")
    else:
        candidates = sorted(mds, key=lambda n: n.count("/"))
        if not candidates:
            raise fetch.FetchError(f"仓库里没有 .md 技能文件：{source}")
        depth = candidates[0].count("/")
        picked = [(n, n) for n in candidates if n.count("/") == depth]

    keys: list[str] = []
    seen: set[str] = set()
    for full, rel in picked:
        parts = Path(rel).parts
        if len(parts) > 1:
            parts = parts[1:]  # 剥掉 zip 顶层目录（GitHub 打包总是 repo-main/... 开头）
        stem = Path(parts[-1]).stem
        flat = "_".join([*parts[:-1], stem])
        key = _safe_name(f"{name}_{flat}" if name else flat)
        if key in seen:
            continue
        seen.add(key)
        _write_skill(key, _member_bytes(payload, full))
        keys.append(key)
    if not keys:
        raise fetch.FetchError(f"仓库里没有可安装的技能：{source}（分支 {ref}）")
    return keys


def _member_bytes(payload: bytes, member: str) -> bytes:
    import io
    import zipfile

    with zipfile.ZipFile(io.BytesIO(payload)) as zf:
        return zf.read(member)
