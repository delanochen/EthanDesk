"""打包入口：双击 exe / python launcher.py 都从这里起统一客户端窗口。

单独包一层是为了让 PyInstaller 只依赖一个明确的入口文件，
而不是去猜包内 ``__main__``。
"""

from __future__ import annotations

import multiprocessing
import os
import sys


def _setup_frozen_paths() -> None:
    """onefile 解包目录里 assets/ 也要能被托盘图标找到。"""
    if getattr(sys, "frozen", False):
        bundle = getattr(sys, "_MEIPASS", os.path.dirname(sys.executable))
        assets = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "assets")
        if not os.path.isdir(assets):
            os.environ.setdefault("ETHANDESK_ASSETS", os.path.join(bundle, "assets"))


def main() -> None:
    multiprocessing.freeze_support()
    _setup_frozen_paths()
    from desk.client import main as desk_main

    desk_main()


if __name__ == "__main__":
    main()
