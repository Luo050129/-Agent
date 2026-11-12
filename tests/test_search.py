"""搜索测试：三个来源能不能收全、关键词匹配准不准、排序合不合理。

`python tests/test_search.py` 直接跑。
"""

from __future__ import annotations

import json
import os
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from miniagent.core import Session  # noqa: E402
from miniagent.core import search as search_mod  # noqa: E402
from miniagent.core.loop import INTERNAL_PREFIXES  # noqa: E402


def _seed_records() -> Path:
    """造一份记录文件，模拟真实工具调用。"""
    tmp = Path(tempfile.mkdtemp())
    rows = [
        {"ts": "10:00:01", "step": 1, "tool": "list_dir", "args": '{"path": "miniagent"}',
         "result": "目录 miniagent 共 8 项", "blocked": False, "ok": True},
        {"ts": "10:00:02", "step": 2, "tool": "write_file",
         "args": json.dumps({"path": "docs/report.md", "content": "统计结果"}, ensure_ascii=False),
         "result": "已写入 docs/report.md（4 字符）", "blocked": False, "ok": True},
        {"ts": "10:00:03", "step": 3, "tool": "run_shell", "args": '{"command": "pytest -q"}',
         "result": "[ERR] exit_code=1\nstderr:\n1 failed", "blocked": False, "ok": False},
    ]
    (tmp / "20260101-100000.jsonl").write_text(
        "\n".join(json.dumps(r, ensure_ascii=False) for r in rows) + "\n", encoding="utf-8"
    )
    return tmp


def test_collect() -> list[tuple[str, bool]]:
    tmp = _seed_records()

    session = Session(system="你是助手")
    session.add_user("统计一下代码行数")
    session.messages.append({"role": "assistant", "content": "一共 1709 行"})
    # 内核注入的指令不该出现在搜索结果里
    session.add_user(f"{INTERNAL_PREFIXES[0]}计划 第 1/2 步] 列目录")

    hits = search_mod.collect(session, records_dir=tmp)
    kinds = [h.kind for h in hits]

    return [
        ("对话被收进来", kinds.count("对话") == 2),
        ("内核指令被过滤掉", all("第 1/2 步" not in h.title for h in hits)),
        ("工具调用算任务", any(h.kind == "任务" and "list_dir" in h.title for h in hits)),
        ("write_file 算产物", any(h.kind == "产物" and "report.md" in h.title for h in hits)),
        ("run_shell 仍算任务", any(h.kind == "任务" and "run_shell" in h.title for h in hits)),
        ("全部三类都有", set(kinds) >= {"对话", "任务", "产物"}),
    ]


def test_search_match() -> list[tuple[str, bool]]:
    hits = [
        search_mod.Hit("对话", "统计代码行数", "帮我统计一下 python 代码行数"),
        search_mod.Hit("产物", "docs/report.md", "已写入 docs/report.md"),
        search_mod.Hit("任务", "run_shell  pytest -q", "跑测试，1 failed"),
    ]

    empty = search_mod.search(hits, "")
    none = search_mod.search(hits, "根本没有这个词")
    code = search_mod.search(hits, "代码")
    upper = search_mod.search(hits, "PYTHON")
    path = search_mod.search(hits, "report")
    detail_only = search_mod.search(hits, "1 failed")

    return [
        ("空关键词不返回结果", empty == []),
        ("搜不到返回空", none == []),
        ("能匹配标题", len(code) == 1 and code[0].kind == "对话"),
        ("能匹配详情", len(upper) == 1),
        ("大小写不敏感", len(path) == 1 and path[0].kind == "产物"),
        ("详情里的内容也能搜到", len(detail_only) == 1),
        ("对话排在最前面", search_mod.search(hits, "统计")[0].kind == "对话"),
    ]


def test_limits() -> list[tuple[str, bool]]:
    many = [search_mod.Hit("对话", f"问题{i}", "内容") for i in range(200)]
    result = search_mod.search(many, "问题")
    return [
        ("结果有上限", len(result) <= search_mod.MAX_HITS),
        ("上限是 80", search_mod.MAX_HITS == 80),
    ]


def test_kinds() -> list[tuple[str, bool]]:
    """分类筛选：给了 kinds 就只在那几类里找。"""
    hits = [
        search_mod.Hit("对话", "统计行数", "统计一下代码行数"),
        search_mod.Hit("任务", "list_dir 统计", "list_dir 统计目录"),
        search_mod.Hit("产物", "统计报告.md", "统计报告.md"),
        search_mod.Hit("对话", "完全无关", "今天天气不错"),
    ]

    only_chat = search_mod.search(hits, "统计", kinds=["对话"])
    only_task = search_mod.search(hits, "统计", kinds=["任务"])
    both = search_mod.search(hits, "统计", kinds=["任务", "产物"])
    everything = search_mod.search(hits, "统计")
    tally = search_mod.counts(hits)

    return [
        ("只搜对话", len(only_chat) == 1 and only_chat[0].kind == "对话"),
        ("只搜任务", len(only_task) == 1 and only_task[0].kind == "任务"),
        ("可以多选分类", len(both) == 2),
        ("不给分类就是全部", len(everything) == 3),
        ("分类顺序是对话-任务-产物", search_mod.KINDS == ("对话", "任务", "产物")),
        ("结果仍按类别排序", [h.kind for h in everything] == ["对话", "任务", "产物"]),
        ("counts 数对了", tally == {"对话": 2, "任务": 1, "产物": 1}),
    ]


def main() -> int:
    print("[search] 开始")
    checks = test_collect() + test_search_match() + test_limits() + test_kinds()
    for label, ok in checks:
        print(f"  {'PASS' if ok else 'FAIL'}  {label}")
    ok_all = all(ok for _, ok in checks)
    print(f"[search] {'全部通过' if ok_all else '存在失败项'}")
    return 0 if ok_all else 1


if __name__ == "__main__":
    raise SystemExit(main())
