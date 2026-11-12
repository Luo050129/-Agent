"""限额校准：跑几类代表任务，量出步数、单步耗时、token 消耗。

改限额之前先跑这个，用自己机器和当前模型的数据说话：

    python tests/bench_limits.py          # 默认 4 个任务
    python tests/bench_limits.py --all    # 加上重任务

注意：这不是单元测试，会真实调用模型、产生费用（几毛钱量级）。
"""

from __future__ import annotations

import json
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from miniagent.config import load_env  # noqa: E402
from miniagent.core import Agent, Session, Sink  # noqa: E402
from miniagent.core.context import RecordLog, estimate_text  # noqa: E402
from miniagent.core.guard import Limits  # noqa: E402
from miniagent.model import LLM  # noqa: E402

TASKS = [
    ("查目录", "看一下当前目录里有哪些文件夹，一句话说清"),
    ("统计代码", "统计 miniagent 目录有多少行 python 代码"),
    ("技能审码", "用 code-review 技能看一下 miniagent/core/guard.py，只给一条最该改的"),
    ("读文档", "把 README.md 里的测试命令列出来"),
]

HEAVY_TASKS = [
    ("多步排查", "检查 miniagent/core 下每个文件的行数，找出最长的那个，并说它主要负责什么"),
]


class Meter(Sink):
    """只计量，不显示。"""

    def __init__(self) -> None:
        self.steps: list[float] = []
        self.tools = 0
        self.out_tokens = 0
        self.tool_chars = 0
        self._last = time.time()

    def text(self, piece: str) -> None:
        self.out_tokens += estimate_text(piece)

    def tool_result(self, result: str) -> None:
        self.tools += 1
        self.tool_chars += len(result)

    def step_done(self, step: int) -> None:
        now = time.time()
        self.steps.append(now - self._last)
        self._last = now

    def reset_clock(self) -> None:
        self._last = time.time()


def run_one(name: str, prompt: str) -> dict:
    session = Session()
    session.add_user(prompt)
    meter = Meter()
    meter.reset_clock()
    agent = Agent(
        LLM(),
        max_steps=30,
        limits=Limits(max_steps=30, max_tool_calls=80, max_seconds=600),
        self_check=False,
        records=RecordLog(enabled=False),
    )
    start = time.time()
    agent.run(session, sink=meter)
    total = time.time() - start
    return {
        "task": name,
        "steps": len(meter.steps),
        "tools": meter.tools,
        "total_s": round(total, 1),
        "step_avg_s": round(sum(meter.steps) / len(meter.steps), 1) if meter.steps else 0.0,
        "step_max_s": round(max(meter.steps), 1) if meter.steps else 0.0,
        "out_tokens": meter.out_tokens,
        "tool_chars_avg": int(meter.tool_chars / meter.tools) if meter.tools else 0,
    }


def main() -> int:
    load_env()
    tasks = TASKS + (HEAVY_TASKS if "--all" in sys.argv else [])

    print(f"{'任务':<10}{'步数':>4}{'工具':>6}{'总耗时':>9}{'单步均':>8}{'单步最大':>9}{'输出tok':>9}{'结果均值':>9}")
    rows = []
    for name, prompt in tasks:
        row = run_one(name, prompt)
        rows.append(row)
        print(
            f"{row['task']:<10}{row['steps']:>4}{row['tools']:>6}{row['total_s']:>8.1f}s"
            f"{row['step_avg_s']:>7.1f}s{row['step_max_s']:>8.1f}s"
            f"{row['out_tokens']:>9}{row['tool_chars_avg']:>9}"
        )

    steps = [r["steps"] for r in rows if r["steps"]]
    lat = [r["step_avg_s"] for r in rows if r["step_avg_s"]]
    if steps and lat:
        p_max_steps = max(steps)
        p95_lat = sorted(lat)[max(0, int(len(lat) * 0.95) - 1)]
        print("\n按本次数据推的限额（取最大观测值的 2~3 倍做余量）：")
        print(f"  max_tool_calls ≈ {max(p_max_steps * 3, 12)}（本次最大 {p_max_steps} 步）")
        print(f"  max_seconds    ≈ {max(int(p95_lat * p_max_steps * 4), 60)}（单步 {p95_lat:.1f}s × {p_max_steps} 步 × 4）")
        print("  真实限额还要考虑工具超时（命令最长 60s）和上下文预算，见 README「这些阈值是怎么定的」")

    with open("_tmp/calib.json", "w", encoding="utf-8") as fh:
        json.dump(rows, fh, ensure_ascii=False, indent=2)
    print("\n明细已写入 _tmp/calib.json")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
