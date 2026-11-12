"""工具层：编辑检查点与撤销。

敢让 Agent 自动改代码的前提是「改坏了能一键还原」。write_file 覆盖前先
快照原内容到 .agent/checkpoints/（环形保留最近 20 次），用户通过 GUI 的
「撤销」按钮或 CLI 的 /undo 回退到上一个状态。

语义约定：
  - 撤销最近一次写入：文件之前存在 → 还原原内容；之前不存在 → 删掉它
  - 撤销成功后该检查点移除，再按一次撤销的是上一次写入（链式回退）
  - 撤销失败时检查点保留，可以重试
  - 快照先读后落盘：真正的写入失败时不产生检查点（否则会留下
    「撤销一个没发生的写入」这种假记录）
  - 原内容读不出来（二进制 / 权限）就不做检查点——宁可没有，不给假保证
  - run_shell 重定向写的文件不走这条路，是已知盲区，见 README

检查点本身也是普通文件操作，不存进程外的秘密，目录在 .agent/ 下随项目走。
"""

from __future__ import annotations

import json
import time
from pathlib import Path

from ..config import CHECKPOINT_DIR
from ..markers import ERR

MAX_CHECKPOINTS = 20

# 可被测试覆盖：patch 这个名字就能把存档引到临时目录
CHECKPOINT_DIR = CHECKPOINT_DIR


def _dir() -> Path:
    return CHECKPOINT_DIR


def _files() -> list[Path]:
    """按文件名（时间戳序）列出所有检查点。"""
    d = _dir()
    if not d.is_dir():
        return []
    return sorted(d.glob("*.json"))


def snapshot(path: str | Path) -> dict | None:
    """读取目标的原始状态。返回 None 表示无法无损快照（或不该记录）。"""
    target = Path(str(path)).expanduser()
    try:
        existed = target.exists()
        content = target.read_text(encoding="utf-8") if existed else None
    except (OSError, UnicodeDecodeError, ValueError):
        return None  # 二进制或读不出：不快照，不给假保证
    return {"ts": time.strftime("%Y-%m-%d %H:%M:%S"), "path": str(target), "existed": existed, "content": content}


def save(snap: dict | None) -> str | None:
    """把快照落盘。返回检查点 id；失败静默（检查点不能挡正常写入）。"""
    if snap is None:
        return None
    try:
        d = _dir()
        d.mkdir(parents=True, exist_ok=True)
        cid = str(time.time_ns())
        (d / f"{cid}.json").write_text(json.dumps(snap, ensure_ascii=False), encoding="utf-8")
        _prune()
        return cid
    except OSError:
        return None


def record(path: str | Path) -> str | None:
    """一步到位：快照 + 落盘。给不经过 write_file 的写入方用。"""
    return save(snapshot(path))


def _prune() -> None:
    files = _files()
    for old in files[: max(0, len(files) - MAX_CHECKPOINTS)]:
        try:
            old.unlink(missing_ok=True)
        except OSError:
            pass


def undo_last() -> str:
    """撤销最近一次写入，返回给人话结果。"""
    files = _files()
    if not files:
        return "没有可撤销的写入。"
    latest = files[-1]
    try:
        data = json.loads(latest.read_text(encoding="utf-8"))
        path = Path(data["path"])
        existed = bool(data["existed"])
        content = data.get("content")
    except (OSError, json.JSONDecodeError, KeyError):
        # 检查点文件本身坏了：清掉它，别让它永远堵在栈顶
        latest.unlink(missing_ok=True)
        return f"{ERR}检查点损坏，已跳过。再按一次撤销可回退更早的写入。"

    try:
        if existed:
            if content is None:
                return f"{ERR}该检查点没有内容快照，无法还原（{path}）。"
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(content, encoding="utf-8")
            message = f"已还原：{path}（撤销 {data['ts']} 的写入，恢复原内容 {len(content)} 字符）"
        else:
            path.unlink(missing_ok=True)
            message = f"已删除：{path}（它在该次写入前不存在）"
    except OSError as exc:
        return f"{ERR}撤销失败：{exc}（检查点保留，可重试）"

    latest.unlink(missing_ok=True)
    return message


def pending_count() -> int:
    return len(_files())


def recent(limit: int = 8) -> list[dict]:
    """最近的写入记录（新的在前），给界面展示用。"""
    out: list[dict] = []
    for f in reversed(_files()[-limit:]):
        try:
            data = json.loads(f.read_text(encoding="utf-8"))
            out.append({"ts": data.get("ts", ""), "path": data.get("path", "")})
        except (OSError, json.JSONDecodeError):
            continue
    return out
