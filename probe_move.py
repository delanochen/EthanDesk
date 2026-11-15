"""只发一条鼠标移动，用来核对归一化坐标到物理像素的映射。

用法：python probe_move.py [x] [y]   （x/y 为 0~1 归一化坐标）
"""

from __future__ import annotations

import asyncio
import os
import sys

from viewer.client import DeskClient
from common.defaults import DEFAULT_VIEWER_URL
from agent.config import AgentConfig

# 口令不进仓库：跑之前设 ETHANDESK_RELAY_PASSWORD。
PASSWORD = os.environ.get("ETHANDESK_RELAY_PASSWORD", "")


async def main() -> None:
    x = float(sys.argv[1]) if len(sys.argv) > 1 else 0.05
    y = float(sys.argv[2]) if len(sys.argv) > 2 else 0.05
    config = AgentConfig.load()
    client = DeskClient(DEFAULT_VIEWER_URL)
    await client.login(PASSWORD)
    session = await client.open_session(config.agent_id, config.session_code)
    await session.send_text({"t": "set_quality", "scale": 0.4, "quality": 45, "fps": 10})
    await asyncio.sleep(1.0)
    await session.send_text({"t": "input", "ev": "mouse_move", "x": x, "y": y})
    print(f"sent mouse_move {x},{y}", flush=True)
    await asyncio.sleep(2.0)
    await session.close()
    await client.close()

asyncio.run(main())
