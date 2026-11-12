"""工具层：读写文件与列目录。"""

from __future__ import annotations

from pathlib import Path

from . import checkpoints
from ._util import ERR, truncate  # 标记单一来源见 ../markers.py


def read_file(path: str, start: int = 1, limit: int = 300) -> str:
    """按行读文本文件，带行号。"""
    p = Path(path).expanduser()
    if not p.is_file():
        return f"{ERR}文件不存在或不是文件：{p}"
    try:
        lines = p.read_text(encoding="utf-8", errors="replace").splitlines()
    except OSError as exc:
        return f"{ERR}读取失败：{exc}"

    start = max(1, start)
    chunk = lines[start - 1 : start - 1 + max(1, limit)]
    if not chunk:
        return f"{ERR}文件共 {len(lines)} 行，指定区间为空。"

    body = "\n".join(f"{start + i:>5}| {line}" for i, line in enumerate(chunk))
    tail = "" if start - 1 + limit >= len(lines) else f"\n...（共 {len(lines)} 行）"
    return truncate(body + tail)


def write_file(path: str, content: str) -> str:
    """写文件，自动创建父目录，覆盖写。覆盖前快照，支持撤销（/undo）。"""
    p = Path(path).expanduser()
    snap = checkpoints.snapshot(p)  # 先快照；快照失败不挡写入，只是不能撤销这一次
    try:
        if p.parent and not p.parent.exists():
            p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(content, encoding="utf-8")
    except OSError as exc:
        return f"{ERR}写入失败：{exc}"
    checkpoints.save(snap)  # 写入成功才落盘检查点，避免「撤销一个没发生的写入」
    return f"已写入 {p}（{len(content)} 字符）"


def list_dir(path: str = ".") -> str:
    """列目录，目录带 / 后缀，文件带字节数。"""
    p = Path(path).expanduser()
    if not p.is_dir():
        return f"{ERR}目录不存在：{p}"

    entries = []
    for item in sorted(p.iterdir(), key=lambda x: (not x.is_dir(), x.name.lower())):
        if item.is_dir():
            entries.append(f"[DIR]  {item.name}/")
        else:
            try:
                size = item.stat().st_size
            except OSError:
                size = -1
            entries.append(f"[FILE] {item.name}  ({size} B)")
    return truncate(f"目录 {p.resolve()} 共 {len(entries)} 项：\n" + "\n".join(entries))


SCHEMAS = [
    {
        "type": "function",
        "function": {
            "name": "read_file",
            "description": "按行读取文本文件内容（带行号）。需要看代码、日志、配置时使用。",
            "parameters": {
                "type": "object",
                "properties": {
                    "path": {"type": "string", "description": "文件路径"},
                    "start": {"type": "integer", "description": "起始行号，从 1 开始"},
                    "limit": {"type": "integer", "description": "最多读多少行，默认 300"},
                },
                "required": ["path"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "write_file",
            "description": "写入（覆盖）文本文件，父目录不存在会自动创建。生成代码、报告、配置文件时使用。",
            "parameters": {
                "type": "object",
                "properties": {
                    "path": {"type": "string", "description": "文件路径"},
                    "content": {"type": "string", "description": "完整文件内容"},
                },
                "required": ["path", "content"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "list_dir",
            "description": "列出目录内容，区分目录和文件并给出大小。不确定文件在哪时先列目录。",
            "parameters": {
                "type": "object",
                "properties": {"path": {"type": "string", "description": "目录路径，默认当前目录"}},
            },
        },
    },
]

HANDLERS = {"read_file": read_file, "write_file": write_file, "list_dir": list_dir}
DANGEROUS = {"write_file"}
