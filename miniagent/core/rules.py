"""核心层：边界。

两类边界，一个软一个硬：

  规则边界（软）—— agent.md 里的约定，注入系统提示词，靠模型遵守。
  权限边界（硬）—— 读写目录白名单 + shell 命令黑名单，在调用工具前直接拦，不靠自觉。
"""

from __future__ import annotations

import os
import re
from dataclasses import dataclass, field
from pathlib import Path

from ..config import BASE_DIR, env_paths, rules_budget, rules_file

# shell 黑名单：命中就拒绝执行
SHELL_DENY: tuple[tuple[str, str], ...] = (
    (r"rm\s+(-[a-z]*[rf][a-z]*\s+)+(/|~|\*?$)", "递归删除根目录或家目录"),
    (r"del\s+/[sq].*[a-z]:\s*\\?\s*$", "递归删除整个盘"),
    (r"rd\s+/s.*[a-z]:\s*\\?\s*$", "删除整个盘目录树"),
    (r"format\s+[a-z]:", "格式化磁盘"),
    (r"mkfs\.", "格式化分区"),
    (r"diskpart", "磁盘分区工具"),
    (r"dd\s+if=.*of=/dev/[sh]d", "直接写入磁盘设备"),
    (r"\bshutdown\b", "关机"),
    (r"\breboot\b", "重启"),
    (r"\bsetx\b", "持久化写入用户环境变量"),
    (r"reg\s+delete\s+hklm", "删除系统注册表项"),
    (r"remove-item\s+.*-recurse\s+.*[a-z]:\\?\s*$", "递归删除整个盘"),
)

# 代码执行黑名单：明显越界的调用也拦一道
CODE_DENY: tuple[tuple[str, str], ...] = (
    (r"shutil\.rmtree\(\s*['\"](?:/|[a-z]:\\\\?\s*['\"])", "递归删除根目录"),
    (r"os\.system\(\s*['\"]format", "格式化磁盘"),
    (r"subprocess\.[a-z_]+\(.*\bshutdown\b", "关机"),
)

# shell 里的写目标：重定向和复制类命令，用来防止绕过文件白名单
_WRITE_TARGET = re.compile(
    r"(?:>>?|\btee\b|\bcopy\b|\bmove\b|\bxcopy\b|\brobocopy\b)\s+(\"[^\"]+\"|'[^']+'|[^\s|;&>]+)"
)
_ABS_PATH = re.compile(r"^(?:[a-zA-Z]:[\\/]|\\\\|/)")

# shell 里的读目标：读文件类命令 + 绝对路径。没有这一道，`type C:\xxx` 就绕过了读白名单
_READ_TARGET = re.compile(
    r"\b(?:type|cat|more|head|tail|get-content)\s+(\"[^\"]+\"|'[^']+'|[^\s|;&>]+)",
    re.I,
)


def _extract_write_targets(command: str) -> list[str]:
    targets = []
    for match in _WRITE_TARGET.finditer(command):
        raw = match.group(1).strip().strip("\"'")
        if raw and _ABS_PATH.match(raw):
            targets.append(raw)
    return targets


def _extract_read_targets(command: str) -> list[str]:
    targets = []
    for match in _READ_TARGET.finditer(command):
        raw = match.group(1).strip().strip("\"'")
        if raw and _ABS_PATH.match(raw):
            targets.append(raw)
    return targets


def _norm(path: Path) -> Path:
    try:
        return Path(os.path.normcase(str(path.resolve())))
    except OSError:
        return Path(os.path.normcase(str(path)))


def inside(path: str | Path, roots: list[Path]) -> bool:
    """路径是否落在允许的目录里。"""
    target = _norm(Path(str(path)).expanduser())
    for root in roots:
        root_n = _norm(root)
        if target == root_n or root_n in target.parents:
            return True
    return False


_SECTION_RE = re.compile(r"^(#{1,3})\s+(.+)$", re.M)
_cache: dict = {"stamp": None, "sections": []}


def _load_sections(path: Path) -> list[str]:
    """按标题把规则文件切成若干节，每节是一整块文本。

    没有标题就整篇算一节。切分只用于预算取舍，不改内容。
    """
    text = path.read_text(encoding="utf-8")
    marks = list(_SECTION_RE.finditer(text))
    if not marks:
        body = text.strip()
        return [body] if body else []

    blocks: list[str] = []
    head = text[: marks[0].start()].strip()
    if head:
        blocks.append(head)
    for index, mark in enumerate(marks):
        end = marks[index + 1].start() if index + 1 < len(marks) else len(text)
        block = text[mark.start() : end].strip()
        if block:
            blocks.append(block)
    return blocks


def agent_rules(budget_tokens: int | None = None) -> str:
    """运行时规则，按预算裁好返回。

    只读 rules.md。agent.md 是给编码助手看的开发约定（两千多 token），
    每轮请求都发一遍纯属烧钱，运行时不读它。

    裁剪按「章节」整段取舍，不切半句 —— 半条规则比没有规则更危险。
    """
    from .context import estimate_text  # 延迟导入，避免模块级循环

    path = rules_file()
    if not path.is_file():
        return ""
    try:
        stat = path.stat()
    except OSError:
        return ""

    stamp = (str(path), stat.st_mtime_ns, stat.st_size)
    if _cache["stamp"] != stamp:
        try:
            _cache["sections"] = _load_sections(path)
        except OSError:
            return ""
        _cache["stamp"] = stamp

    sections: list[str] = _cache["sections"]  # type: ignore[assignment]
    if not sections:
        return ""

    limit = budget_tokens or rules_budget()
    kept: list[str] = []
    used = 0
    dropped = 0
    for body in sections:
        cost = estimate_text(body)
        # 第一节无论如何都留，否则规则文件写错个标题就变成一条规则都不注入
        if kept and used + cost > limit:
            dropped += 1
            continue
        used += cost
        kept.append(body)

    if not kept:  # 第一节本身就超预算：只截断它
        return sections[0][: max(80, limit * 2)]

    text = "\n\n".join(kept)
    if dropped:
        text += f"\n\n（另有 {dropped} 节规则因预算未注入，完整内容见 {path.name}）"
    return text


# 敏感操作的补充说明，用在确认弹窗里，让用户看清代价
_NET_MARKERS = ("curl", "wget", "invoke-webrequest", "ssh", "scp", "ftp ", "telnet", "nc ")
_DEL_MARKERS = ("del ", "rmdir", "rd ", "rm ", "move ", "ren ")


def risk_hint(tool: str, args: dict) -> str:
    """给敏感操作补一句「你到底在允许什么」。"""
    if tool == "write_file":
        path = str(args.get("path") or "")
        try:
            exists = bool(path) and Path(path).expanduser().exists()
        except OSError:
            exists = False
        return "会覆盖已有文件" if exists else "会新建文件"

    if tool == "run_shell":
        command = str(args.get("command") or "").lower()
        hits: list[str] = []
        if any(mark in command for mark in _NET_MARKERS):
            hits.append("涉及网络传输")
        if any(mark in command for mark in _DEL_MARKERS):
            hits.append("涉及删除或移动文件")
        if ">" in command:
            hits.append("会写入文件")
        return "、".join(hits) or "会在本机执行命令"

    if tool == "run_python":
        return "会执行任意 Python 代码"

    return "这是插件提供的第三方能力"


@dataclass
class Policy:
    read_roots: list[Path] = field(default_factory=list)
    write_roots: list[Path] = field(default_factory=list)
    allow_shell: bool = True

    @classmethod
    def default(cls) -> "Policy":
        default_roots = [BASE_DIR, Path.cwd()]
        return cls(
            read_roots=env_paths("AGENT_READ_ROOTS", default_roots),
            write_roots=env_paths("AGENT_WRITE_ROOTS", [BASE_DIR]),
            allow_shell=True,
        )

    def allow_read(self, path: str | Path) -> None:
        """用户主动指定的文件临时加白：只放开这一个路径，不放开整个目录。"""
        target = Path(str(path)).expanduser()
        try:
            target = target.resolve()
        except OSError:
            pass
        if target not in self.read_roots:
            self.read_roots.append(target)

    def check(self, tool: str, args: dict) -> str | None:
        """返回拒绝理由；放行返回 None。"""
        if tool in ("read_file", "read_document", "list_dir"):
            path = args.get("path") or "."
            if not inside(path, self.read_roots):
                return f"读取范围之外，只允许读：{self._roots_text(self.read_roots)}"
            return None

        if tool == "write_file":
            path = args.get("path") or ""
            if not path:
                return "缺少 path 参数"
            if not inside(path, self.write_roots):
                return f"写入范围之外，只允许写：{self._roots_text(self.write_roots)}"
            return None

        if tool == "run_shell":
            command = args.get("command") or ""
            denied = self._check_command(command, SHELL_DENY, "命令")
            if denied:
                return denied
            # 重定向 / copy 的绝对路径目标也要落在写白名单里，否则白名单形同虚设
            for target in _extract_write_targets(command):
                if not inside(target, self.write_roots):
                    return f"命令要写入 {target}，超出写入白名单"
            # type / cat / get-content 的绝对路径目标同理，不许读出白名单
            for target in _extract_read_targets(command):
                if not inside(target, self.read_roots):
                    return f"命令要读取 {target}，超出读取白名单"
            return None

        if tool == "run_python":
            return self._check_command(args.get("code") or "", CODE_DENY, "代码")

        return None  # 其他（含插件工具）交给安全模式确认

    def _check_command(self, text: str, deny: tuple[tuple[str, str], ...], label: str) -> str | None:
        low = text.lower()
        for pattern, reason in deny:
            if re.search(pattern, low):
                return f"{label}命中黑名单：{reason}"
        if label == "命令" and not self.allow_shell:
            return "当前策略禁止执行 shell 命令"
        return None

    @staticmethod
    def _roots_text(roots: list[Path]) -> str:
        return "、".join(str(r) for r in roots) if roots else "（未配置）"
