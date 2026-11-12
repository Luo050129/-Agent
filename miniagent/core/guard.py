"""核心层：熔断与限流。

一个 Agent 跑飞通常是这几种：同一调用转圈、反复失败还硬试、无限刷工具、
模型慢到天荒地老、被边界连拒还继续。这里把它们收成一张网：

    before_tool  执行前问一句「还允许吗」
    after_tool   执行后记账「成没成、有没有进展」
    check_stop   每步结束问一句「该不该收手」

收手不抛异常，而是产出一份人话报告作为答复 —— 用户要知道卡在哪，而不是拿到一片空白。
"""

from __future__ import annotations

import hashlib
import json
import os
import time
from collections import Counter, deque
from dataclasses import dataclass, field

from ..markers import ERR_TAG, OK_TAG  # 标记单一来源，tools/mcp 也从这里拿

# ---------------------------------------------------------------- 失败判定
#
# 旧的判定是「结果里出现某个词就算失败」，两个毛病：
#   误判 —— 正常内容里恰好含「不存在」「超出」，比如读一份讲容灾的文档
#   漏判 —— 新写的工具换了措辞，词表没同步
#
# 现在改成三层，由确定到模糊：
#   1. 工具主动打 [ERR] / [OK] 前缀  → 直接采信，零歧义
#   2. 没有前缀的（第三方插件）      → 只看开头 200 字符做关键词兜底
#   3. 都没有                        → 当作成功，交给模型自己判断
#
# 前缀对模型也是有效信号，所以不剥掉，原样回灌。

ERR_PREFIX = ERR_TAG
OK_PREFIX = OK_TAG
LEGACY_HEAD = 200  # 无前缀时只在前这么多字符里找失败词，避免正文误伤

# 兜底词表：只保留「出现在开头就基本等于失败」的强信号
FAIL_MARKERS = (
    "执行异常",
    "参数不匹配",
    "超时",
    "失败",
    "不存在",
    "exit_code=1",
    "被规则拒绝",
    "被熔断拦截",
    "解析失败",
    "无法还原",
    "不支持",
)


def classify(result: str) -> str:
    """三态：error / ok / unknown（按结构化前缀判）。"""
    head = (result or "")[:40].lstrip()
    if head.startswith(ERR_PREFIX):
        return "error"
    if head.startswith(OK_PREFIX):
        return "ok"
    return "unknown"


def is_failure(result: str) -> bool:
    """调用是否失败。有前缀就采信前缀，没有才退回关键词。"""
    kind = classify(result)
    if kind == "error":
        return True
    if kind == "ok":
        return False
    head = (result or "")[:LEGACY_HEAD]
    return any(mark in head for mark in FAIL_MARKERS)


def _env_int(name: str, default: int) -> int:
    try:
        return max(1, int(os.getenv(name, "")))
    except ValueError:
        return default


def _env_float(name: str, default: float) -> float:
    try:
        return max(1.0, float(os.getenv(name, "")))
    except ValueError:
        return default


@dataclass
class Limits:
    """一次任务的硬上限，全部可用环境变量覆盖。

    这些数不是拍的，每个都有约束来源（详见 README「这些阈值是怎么定的」）：

    max_same_call=3
        完全相同的 (工具, 参数) 在无副作用时返回相同结果，第 2 次信息增益≈0。
        给到 3 是为了容忍有外部状态的工具（列目录、抓网页）。按信息增益定的，不是按次数拍的。
    max_failures_per_call=2
        失败要分瞬时和确定性：瞬时（网络、超时）值得再试一次，确定性（不存在、越界）第二次就确认了。
        2 = 1 次容错 + 1 次确认。
    max_unproductive=4
        「连续相同结果」至少 3 次才成立，加一次容错。
    max_fail_streak=6
        一轮通常并行 2~3 个工具，6 = 连续两轮全失败，说明工具链整体不可用。
    max_tool_calls=40
        与上下文预算对齐：单步约 1.8k token（工具结果上限 3k 字符≈1.5k + 参数回复），
        40 × 1.8k = 72k ≈ 32k 预算的 2.2 倍 —— 再多的调用只会把早期上下文挤成摘要。
        实测：24 个真实会话工具调用中位数 1、最大 6；4 类代表任务最多 4 次。
    max_seconds=300
        时间预算不是性能调优，是防挂死的保险丝：单个工具最长 60s，300s 至少容得下 3 次超时。
        实测单步 0.8~1.9s（DeepSeek V4 Flash），正常任务几秒就结束，撞不到这个上限。
    """

    max_steps: int = 20
    max_tool_calls: int = 40
    max_seconds: float = 300.0
    max_same_call: int = 3  # 同一工具 + 同一参数最多出现几次
    max_failures_per_call: int = 2  # 同一调用最多连续失败几次
    max_denials: int = 3  # 被边界拒绝几次就收手
    max_fail_streak: int = 6  # 连续失败多少次要停
    max_unproductive: int = 4  # 连续拿到相同结果算无进展

    @classmethod
    def from_env(cls) -> "Limits":
        return cls(
            max_steps=_env_int("AGENT_MAX_STEPS", cls.max_steps),
            max_tool_calls=_env_int("AGENT_MAX_TOOL_CALLS", cls.max_tool_calls),
            max_seconds=_env_float("AGENT_MAX_SECONDS", cls.max_seconds),
            max_same_call=_env_int("AGENT_MAX_SAME_CALL", cls.max_same_call),
            max_failures_per_call=_env_int("AGENT_MAX_CALL_FAILURES", cls.max_failures_per_call),
            max_denials=_env_int("AGENT_MAX_DENIALS", cls.max_denials),
        )


@dataclass
class Guard:
    limits: Limits = field(default_factory=Limits.from_env)
    clock: object = time.monotonic

    def __post_init__(self) -> None:
        self.start = self.clock()
        self.tool_calls = 0
        self.calls: Counter[str] = Counter()
        self.failures: Counter[str] = Counter()
        self.denials = 0
        self.fail_streak = 0
        self.unproductive = 0
        self.notes: list[str] = []  # 给报告用
        self._results: deque[str] = deque(maxlen=6)

    # ---------------------------------------------------------- 调用前

    def fingerprint(self, name: str, args: str) -> str:
        try:
            data = json.loads(args, strict=False) if args.strip() else {}
        except json.JSONDecodeError:
            data = args
        try:
            body = json.dumps(data, ensure_ascii=False, sort_keys=True)
        except (TypeError, ValueError):
            body = str(data)
        return f"{name}|{body[:200]}"

    def before_tool(self, name: str, args: str) -> str | None:
        """返回拦截原因；放行返回 None。"""
        if self.tool_calls >= self.limits.max_tool_calls:
            return f"本次任务工具调用已达上限（{self.limits.max_tool_calls} 次）"

        key = self.fingerprint(name, args)
        if self.failures[key] >= self.limits.max_failures_per_call:
            return f"同一调用已失败 {self.failures[key]} 次，别再重试同一个写法"
        if self.calls[key] >= self.limits.max_same_call:
            return f"同一调用已重复 {self.calls[key]} 次，判定为原地打转"
        return None

    # ---------------------------------------------------------- 调用后

    def after_tool(self, name: str, args: str, result: str, denied: bool = False) -> None:
        key = self.fingerprint(name, args)
        self.tool_calls += 1
        self.calls[key] += 1

        failed = denied or is_failure(result)
        if failed:
            self.failures[key] += 1
            self.fail_streak += 1
            self.notes.append(f"{name} 失败：{result.splitlines()[0][:60]}")
        else:
            self.fail_streak = 0

        if denied:
            self.denials += 1
            self.notes.append(f"{name} 被边界拦截")

        digest = hashlib.sha1(result.encode("utf-8", "replace")).hexdigest()
        self.unproductive = self.unproductive + 1 if digest in self._results else 0
        self._results.append(digest)

    def reset_progress(self, reason: str = "") -> None:
        """方向变了，把「原地打转」类计数清零。

        保留 tool_calls（那是预算，花掉的钱退不回来），只清掉
        「连续失败 / 无进展 / 被拒 / 同一调用重复」这些跟旧方向绑定的判断。
        用户说「换个思路重来」时不清这些，会把新路子直接熔断。
        """
        self.fail_streak = 0
        self.unproductive = 0
        self.denials = 0
        self.failures.clear()
        self.calls.clear()
        self._results.clear()
        if reason:
            self.notes.append(reason)

    # ---------------------------------------------------------- 收手判定

    def elapsed(self) -> float:
        return self.clock() - self.start

    def check_stop(self) -> str | None:
        """该收手就返回原因。"""
        if self.elapsed() > self.limits.max_seconds:
            return f"超出时间预算（{self.limits.max_seconds:.0f} 秒）"
        if self.denials >= self.limits.max_denials:
            return f"连续被边界拒绝 {self.denials} 次"
        if self.fail_streak >= self.limits.max_fail_streak:
            return f"连续失败 {self.fail_streak} 次"
        if self.unproductive >= self.limits.max_unproductive:
            return f"连续 {self.unproductive} 次拿到相同结果，判定无进展"

        # 同一调用转太多圈：不必等下一次模型请求，当场收手
        for key, count in self.calls.items():
            if count >= self.limits.max_same_call:
                return f"同一调用重复 {count} 次，判定为原地打转"
        for key, count in self.failures.items():
            if count >= self.limits.max_failures_per_call:
                return f"同一调用已失败 {count} 次，别再重试同一个写法"
        return None

    # ---------------------------------------------------------- 报告

    def report(self, reason: str) -> str:
        """收手时给用户的说明：卡在哪、试过什么、下一步怎么办。"""
        lines = [f"这次没跑完，先停下了：**{reason}**。", "", f"已执行 {self.tool_calls} 次工具调用。"]
        if self.notes:
            lines.append("")
            lines.append("出问题的地方：")
            for note in self.notes[-4:]:
                lines.append(f"- {note}")
        lines.extend(
            [
                "",
                "你可以：换个说法补充关键信息、把任务拆小，或者直接告诉我该用哪个工具/路径。",
            ]
        )
        return "\n".join(lines)
