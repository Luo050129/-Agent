"""图形界面入口。

平时双击 启动GUI.vbs 就行，它会自动找带 tkinter 的解释器。
手动启动：C:\\Python314\\pythonw.exe gui.py
"""

from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from miniagent.ui.window import main  # noqa: E402

if __name__ == "__main__":
    raise SystemExit(main())
