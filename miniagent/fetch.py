"""全包共用的 GitHub 拉取与解压。

插件（plugins/manager.py）和技能（skills/manager.py）都从 GitHub 装东西，
下载和 zip 解析只有一套实现，放这里。零第三方依赖，错误统一抛 FetchError。
"""

from __future__ import annotations

import shutil
import tempfile
import urllib.error
import urllib.request
import zipfile
from pathlib import Path


class FetchError(RuntimeError):
    """下载 / 解压 / 定位失败。"""


def download(url: str, timeout: int = 30) -> bytes:
    req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0 (mini-agent)"})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return resp.read()
    except urllib.error.HTTPError as exc:
        raise FetchError(f"下载失败 HTTP {exc.code}：{url}") from exc
    except (urllib.error.URLError, OSError) as exc:
        raise FetchError(f"下载失败：{exc}") from exc


def github_zip(repo: str, branch: str | None) -> tuple[bytes, str]:
    """下载 GitHub 仓库压缩包，依次尝试指定分支 / main / master。"""
    for ref in ([branch] if branch else []) + ["main", "master"]:
        url = f"https://codeload.github.com/{repo}/zip/refs/heads/{ref}"
        try:
            return download(url), ref
        except FetchError:
            continue
    raise FetchError(f"下载仓库失败（试过 main / master）：{repo}")


def extract_zip(payload: bytes, prefix: str, dest: Path, suffixes: tuple[str, ...] | None = None) -> list[str]:
    """把压缩包里 prefix 下的文件解到 dest。

    suffixes 非空时只解指定扩展名（如 (".md",)）。挡路径穿越，跳过 __pycache__。
    返回解出的相对路径列表。
    """
    with tempfile.TemporaryDirectory(prefix="agent_zip_") as tmp:
        zip_path = Path(tmp) / "repo.zip"
        zip_path.write_bytes(payload)
        with zipfile.ZipFile(zip_path) as zf:
            dest.mkdir(parents=True, exist_ok=True)
            extracted: list[str] = []
            for member in zf.infolist():
                if member.is_dir() or not member.filename.startswith(prefix):
                    continue
                rel = member.filename[len(prefix):]
                if not rel or rel.startswith("__pycache__") or "/__pycache__/" in rel:
                    continue
                if suffixes and not rel.lower().endswith(tuple(suffixes)):
                    continue
                target = (dest / rel).resolve()
                if not str(target).startswith(str(dest.resolve())):
                    continue  # 路径穿越，跳过
                target.parent.mkdir(parents=True, exist_ok=True)
                with zf.open(member) as src, open(target, "wb") as out:
                    shutil.copyfileobj(src, out)
                extracted.append(rel)
            return extracted


def zip_prefix(payload: bytes, subdir: str | None, marker: str) -> str:
    """在压缩包里找含 marker 文件的目录，返回其前缀（以 / 结尾）。"""
    with tempfile.TemporaryDirectory(prefix="agent_zip_") as tmp:
        zip_path = Path(tmp) / "repo.zip"
        zip_path.write_bytes(payload)
        with zipfile.ZipFile(zip_path) as zf:
            if subdir:
                for name in zf.namelist():
                    if name.startswith(subdir.rstrip("/") + "/") and name.endswith(marker):
                        return subdir.rstrip("/") + "/"
                raise FetchError(f"压缩包里没有找到 {subdir}/{marker}")
            entries = [n for n in zf.namelist() if n.endswith("/" + marker) or n == marker]
            if not entries:
                raise FetchError(f"仓库里没有 {marker}")
            return min(entries, key=lambda n: n.count("/"))[: -len(marker)]


def zip_list(payload: bytes) -> list[str]:
    """列出压缩包全部成员名（找 .md 之类的轻量探测用）。"""
    with tempfile.TemporaryDirectory(prefix="agent_zip_") as tmp:
        zip_path = Path(tmp) / "repo.zip"
        zip_path.write_bytes(payload)
        with zipfile.ZipFile(zip_path) as zf:
            return list(zf.namelist())
