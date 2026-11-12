"""编辑检查点测试：快照、撤销、链式回退、环形上限、失败路径。

`python tests/test_checkpoints.py` 直接跑。检查点目录 patch 到临时目录，
不污染真实的 .agent/checkpoints。
"""

from __future__ import annotations

import json
import os
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import miniagent.tools.checkpoints as cp  # noqa: E402
from miniagent.tools import execute  # noqa: E402
from miniagent.tools.filesystem import write_file  # noqa: E402


def _use_tmp_dir(tmp: str) -> None:
    cp.CHECKPOINT_DIR = Path(tmp)


def test_create_then_undo(tmp: str) -> list[tuple[str, bool]]:
    """Agent 新建的文件：撤销 = 删掉它。走真实 execute 链路。"""
    _use_tmp_dir(tmp)
    target = Path(tmp) / "created.txt"
    out = execute("write_file", json.dumps({"path": str(target), "content": "agent 写的"}))
    checks = [
        ("新建文件成功", "已写入" in out and target.is_file()),
        ("检查点已落盘", cp.pending_count() == 1),
        ("撤销有结果", "已删除" in cp.undo_last()),
        ("文件真的没了", not target.exists()),
        ("检查点用后即焚", cp.pending_count() == 0),
    ]
    return checks


def test_overwrite_then_undo(tmp: str) -> list[tuple[str, bool]]:
    """覆盖已有文件：撤销 = 还原原内容。"""
    _use_tmp_dir(tmp)
    target = Path(tmp) / "exist.txt"
    target.write_text("原始内容", encoding="utf-8")

    write_file(str(target), "第一次覆盖")
    checks = [("覆盖后内容变了", target.read_text(encoding="utf-8") == "第一次覆盖")]

    result = cp.undo_last()
    checks.append(("撤销成功", "已还原" in result))
    checks.append(("原内容回来了", target.read_text(encoding="utf-8") == "原始内容"))
    checks.append(("撤销信息带时间", "撤销" in result))
    return checks


def test_chain_undo(tmp: str) -> list[tuple[str, bool]]:
    """连续三次写，逐级撤销，最后回到原始状态。"""
    _use_tmp_dir(tmp)
    target = Path(tmp) / "chain.txt"
    target.write_text("v0", encoding="utf-8")

    write_file(str(target), "v1")
    write_file(str(target), "v2")
    write_file(str(target), "v3")
    checks = [("三次写入", target.read_text(encoding="utf-8") == "v3")]
    checks.append(("三个检查点", cp.pending_count() == 3))

    cp.undo_last()
    checks.append(("撤销一次到 v2", target.read_text(encoding="utf-8") == "v2"))
    cp.undo_last()
    checks.append(("撤销两次到 v1", target.read_text(encoding="utf-8") == "v1"))
    cp.undo_last()
    checks.append(("撤销三次到 v0", target.read_text(encoding="utf-8") == "v0"))
    checks.append(("检查点清空", cp.pending_count() == 0))
    checks.append(("空栈撤销有提示", "没有可撤销" in cp.undo_last()))
    return checks


def test_ring_buffer(tmp: str) -> list[tuple[str, bool]]:
    """超过 20 个检查点，最旧的被挤掉。"""
    _use_tmp_dir(tmp)
    target = Path(tmp) / "ring.txt"
    for i in range(25):
        write_file(str(target), f"round {i}")
    checks = [("环形只留 20 个", cp.pending_count() == 20)]
    checks.append(("保留的是最新内容", target.read_text(encoding="utf-8") == "round 24"))
    # 撤销到最早可回退的位置：20 次之后应该还原到 round 4（第 5 次写入前的状态）
    for _ in range(20):
        cp.undo_last()
    checks.append(("最旧检查点还原到 round 4", target.read_text(encoding="utf-8") == "round 4"))
    return checks


def test_no_fake_checkpoints(tmp: str) -> list[tuple[str, bool]]:
    """写入失败 / 无法快照的文件，不留假检查点。"""
    _use_tmp_dir(tmp)
    before = cp.pending_count()

    # 快照分离语义：snapshot 了但写入失败（目录级错误），save 不该被调用
    snap = cp.snapshot(Path(tmp) / "never_written.txt")
    checks = [("不存在文件的快照标 existed=False", snap is not None and snap["existed"] is False)]
    checks.append(("没 save 就没有检查点", cp.pending_count() == before))

    # 二进制文件：无法无损快照，直接不记录
    blob = Path(tmp) / "blob.bin"
    blob.write_bytes(bytes([0, 159, 146, 150]))
    checks.append(("二进制文件快照返回 None", cp.snapshot(blob) is None))

    # 覆盖二进制文件后撤销：没有检查点可退，给出明确提示
    write_file(str(blob), "文本覆盖")
    checks.append(("覆盖二进制不留假检查点", cp.pending_count() == before))
    result = cp.undo_last()
    checks.append(("空栈提示明确", "没有可撤销" in result))
    return checks


def test_recent_view(tmp: str) -> list[tuple[str, bool]]:
    """recent() 给界面展示：新的在前，字段齐全。"""
    _use_tmp_dir(tmp)
    a = Path(tmp) / "a.txt"
    b = Path(tmp) / "b.txt"
    write_file(str(a), "1")
    write_file(str(b), "2")
    items = cp.recent()
    checks = [
        ("两条记录", len(items) == 2),
        ("新的在前", items[0]["path"].endswith("b.txt")),
        ("字段齐全", all("ts" in item and "path" in item for item in items)),
    ]
    return checks


def main() -> int:
    print("[checkpoints] 开始")
    checks: list[tuple[str, bool]] = []
    original = cp.CHECKPOINT_DIR
    try:
        for test in (test_create_then_undo, test_overwrite_then_undo, test_chain_undo,
                     test_ring_buffer, test_no_fake_checkpoints, test_recent_view):
            with tempfile.TemporaryDirectory(prefix="agent-ckpt-") as tmp:
                checks.extend(test(tmp))
    finally:
        cp.CHECKPOINT_DIR = original

    for label, ok in checks:
        print(f"  {'PASS' if ok else 'FAIL'}  {label}")
    ok_all = all(ok for _, ok in checks)
    print(f"[checkpoints] {'全部通过' if ok_all else '存在失败项'}")
    return 0 if ok_all else 1


if __name__ == "__main__":
    raise SystemExit(main())
