"""配置与路径。所有层都从这里取环境信息，不各自读 os.environ。"""

from __future__ import annotations

import os
from pathlib import Path

# mini_agent/ 目录（包的上层）
BASE_DIR = Path(__file__).resolve().parent.parent

DEFAULT_BASE_URL = "https://api.deepseek.com/v1"
DEFAULT_MODEL = "deepseek-chat"
KEY_ENV_NAMES = ("DEEPSEEK_API_KEY", "OPENAI_API_KEY", "AGENT_API_KEY")

MAX_TOOL_OUTPUT = 8000  # 单条工具结果回灌给模型的字符上限

# 规则文件与运行记录
#
# 两个文件职责不同，别混：
#   agent.md  —— 给 AI 编码助手的开发约定（分层、改码规矩、踩过的坑）。运行时不读。
#   rules.md  —— 运行时规则（权限说明、行为约束）。注入系统提示词，每轮都发，所以要精简。
AGENT_MD = BASE_DIR / "agent.md"
RULES_MD = BASE_DIR / "rules.md"
AGENT_DIR = BASE_DIR / ".agent"
APP_NAME = "墟"
AVATAR_PATH = BASE_DIR / "assets" / "avatar.png"  # 默认用户头像
ICON_SOURCE = BASE_DIR / "assets" / "icon-source.png"  # 侧栏品牌大图（1024 原图）
ICON_PATH = BASE_DIR / "assets" / "icon.ico"  # 窗口和任务栏图标
RECORDS_DIR = AGENT_DIR / "records"
SESSIONS_DIR = AGENT_DIR / "sessions"  # 会话存档：一个会话一个 jsonl，关窗不丢
CHECKPOINT_DIR = AGENT_DIR / "checkpoints"  # 写文件前快照，支持撤销

# MCP：外部工具服务器。配置在 .agent/mcp.json，工具清单缓存在 .agent/mcp-cache/。
# 缓存的意义是「不启动子进程也能知道有哪些工具」，启动时零额外开销。
MCP_CONFIG = AGENT_DIR / "mcp.json"
MCP_CACHE_DIR = AGENT_DIR / "mcp-cache"
DEFAULT_MCP_TOOL_TOKENS = 1500  # 注入给模型的 MCP 工具 schema 总预算
DEFAULT_MCP_TIMEOUT = 20  # 单次 tools/call 的秒数上限

# 规则注入的 token 预算。每轮都要发，给太少会丢约束，给太多白烧钱。
DEFAULT_RULES_TOKENS = 900

DEFAULT_CONTEXT_TOKENS = 32000  # 输入上下文预算，可用 AGENT_CONTEXT_TOKENS 覆盖

# 工作模式：react 边做边看，plan 先规划再执行
MODES = ("react", "plan")
MODE_LABELS = {"react": "ReAct", "plan": "计划执行"}
DEFAULT_MODE = "react"


def default_mode() -> str:
    value = (os.getenv("AGENT_MODE") or DEFAULT_MODE).strip().lower()
    return value if value in MODES else DEFAULT_MODE


def context_budget() -> int:
    raw = os.getenv("AGENT_CONTEXT_TOKENS", "")
    try:
        return max(4000, int(raw))
    except ValueError:
        return DEFAULT_CONTEXT_TOKENS


def rules_file() -> Path:
    """运行时规则文件。AGENT_RULES_FILE 可指向别的路径。"""
    raw = (os.getenv("AGENT_RULES_FILE") or "").strip()
    return Path(raw).expanduser() if raw else RULES_MD


def rules_budget() -> int:
    raw = os.getenv("AGENT_RULES_TOKENS", "")
    try:
        return max(120, int(raw))
    except ValueError:
        return DEFAULT_RULES_TOKENS


def mcp_tool_budget() -> int:
    """MCP 工具 schema 的 token 预算。0 表示全部降级成文字摘要。"""
    raw = os.getenv("AGENT_MCP_TOOL_TOKENS", "")
    try:
        return max(0, int(raw))
    except ValueError:
        return DEFAULT_MCP_TOOL_TOKENS


def mcp_timeout() -> float:
    raw = os.getenv("AGENT_MCP_TIMEOUT", "")
    try:
        return max(1.0, float(raw))
    except ValueError:
        return float(DEFAULT_MCP_TIMEOUT)


def env_paths(name: str, default: list[Path]) -> list[Path]:
    """从环境变量读路径白名单，分号分隔；没配就用默认值。"""
    raw = os.getenv(name, "").strip()
    if not raw:
        return default
    items = [Path(p).expanduser().resolve() for p in raw.split(";") if p.strip()]
    return items or default


def sandbox_enabled() -> bool:
    """进程级沙箱开关。AGENT_SANDBOX=0/false/off 关闭（默认开）。"""
    return os.getenv("AGENT_SANDBOX", "1").strip().lower() not in ("0", "false", "off")


def sandbox_mem_mb() -> int:
    """沙箱子进程内存上限（MB）。0 表示不设限，默认 1024。"""
    raw = os.getenv("AGENT_SANDBOX_MEM_MB", "").strip()
    try:
        return max(0, int(raw)) if raw else 1024
    except ValueError:
        return 1024


class ConfigError(RuntimeError):
    """缺少必要配置（目前只有 API Key）。"""


def load_env() -> None:
    """把 .env 读进 os.environ，不覆盖已有变量。"""
    env_file = BASE_DIR / ".env"
    if not env_file.is_file():
        return
    for line in env_file.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        key = key.strip()
        value = value.strip().strip('"').strip("'")
        if key:
            os.environ.setdefault(key, value)


def api_key(explicit: str | None = None) -> str:
    if explicit:
        return explicit
    for name in KEY_ENV_NAMES:
        value = os.getenv(name)
        if value:
            return value.strip()
    raise ConfigError(
        "没找到 API Key。任选一种：\n"
        "  1) 设置环境变量 DEEPSEEK_API_KEY\n"
        "  2) 在 mini_agent/.env 写一行 DEEPSEEK_API_KEY=sk-xxx\n"
        "  3) 启动时传 --api-key sk-xxx"
    )


def default_model() -> str:
    return os.getenv("AGENT_MODEL") or DEFAULT_MODEL


def default_base_url() -> str:
    return os.getenv("AGENT_BASE_URL") or DEFAULT_BASE_URL
