"""命令行入口。

图形界面请运行 gui.py，或者双击 启动GUI.vbs。
"""

from __future__ import annotations

import argparse
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from miniagent.config import MODES, ConfigError, default_mode, default_model, load_env  # noqa: E402
from miniagent.core import Session, SessionStore  # noqa: E402
from miniagent.model import LLM  # noqa: E402
from miniagent.ui import console  # noqa: E402


def main() -> int:
    here = os.path.dirname(os.path.abspath(__file__))
    os.chdir(here)
    load_env()

    parser = argparse.ArgumentParser(description="墟 —— 有闭环、有边界、可扩展的桌面 Agent（ReAct + 工具调用）")
    parser.add_argument("-p", "--prompt", help="一次性执行，不进入交互模式")
    parser.add_argument("-m", "--model", help=f"模型名，默认 {default_model()}")
    parser.add_argument("--base-url", help="OpenAI 兼容接口地址")
    parser.add_argument("--api-key", help="直接传 API Key")
    parser.add_argument("--max-steps", type=int, default=20, help="单轮最大工具步数，默认 20")
    parser.add_argument("--safe", action="store_true", help="危险操作（shell/写文件/跑代码）执行前确认")
    parser.add_argument(
        "--mode", choices=MODES, default=default_mode(),
        help="工作模式：react 边做边看（默认）/ plan 先规划再执行",
    )
    parser.add_argument(
        "--resume", nargs="?", const="last", default=None, metavar="ID",
        help="恢复历史会话：传会话 ID，或不带参数恢复最近一次",
    )
    parser.add_argument("--no-persist", action="store_true", help="本次对话不落盘存档")
    parser.add_argument("--selftest", action="store_true", help="无 Key 自检整条链路")
    args = parser.parse_args()

    if args.selftest:
        return console.selftest()

    try:
        llm = LLM(model=args.model, base_url=args.base_url, api_key_value=args.api_key)
    except ConfigError as exc:
        print(f"\033[31m{exc}\033[0m")
        return 2

    session = None
    store = None
    if not args.no_persist:
        try:
            if args.resume:
                store = SessionStore.resume(args.resume)
                session = store.load()
                print(f"\033[36m已恢复会话 {store.session_id}（{store.title or '未命名'}）\033[0m")
            else:
                store = SessionStore()
                session = Session()
                store.attach(session)
        except (FileNotFoundError, OSError) as exc:
            print(f"\033[31m{exc}\033[0m")
            return 2

    if args.prompt:
        if not console.one_shot(
            llm, args.prompt, max_steps=args.max_steps, safe=args.safe, mode=args.mode,
            session=session,
        ):
            return 1
    else:
        console.repl(
            llm, max_steps=args.max_steps, safe=args.safe, mode=args.mode,
            session=session, store=store,
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
