"""打包入口（被控端）：python agent_launcher.py 等同于 python -m agent。"""

from __future__ import annotations

import multiprocessing
import os
import sys


def main() -> None:
    multiprocessing.freeze_support()
    from common.dpi import ensure_dpi_aware

    ensure_dpi_aware()
    from agent.app import AgentWindow

    AgentWindow().run()
    _ = os


if __name__ == "__main__":
    main()
    sys.exit(0)
