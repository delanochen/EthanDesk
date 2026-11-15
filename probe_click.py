"""从远控会话点击一个归一化坐标，用来验证点击落地。

用法：python probe_click.py [x] [y] [button]
"""

from __future__ import annotations

import asyncio
import os
import sys

from agent.config import AgentConfig
from common.defaults import DEFAULT_VIEWER_URL
from viewer.client import DeskClient

# 口令不进仓库：跑之前设 ETHANDESK_RELAY_PASSWORD。
PASSWORD = os.environ.get("ETHANDESK_RELAY_PASSWORD", "")


async def main() -> None:
    x = float(sys.argv[1]) if len(sys.argv) > 1 else 0.47
    y = float(sys.argv[2]) if len(sys.argv) > 2 else 0.49
    button = sys.argv[3] if len(sys.argv) > 3 else "left"

    config = AgentConfig.load()
    client = DeskClient(DEFAULT_VIEWER_URL)
    await client.login(PASSWORD)
    session = await client.open_session(config.agent_id, config.session_code)
    await session.send_text({"t": "set_quality", "scale": 0.4, "quality": 45, "fps": 10})
    await asyncio.sleep(1.0)
    await session.send_text({"t": "input", "ev": "mouse_move", "x": x, "y": y})
    await asyncio.sleep(1.0)
    await session.send_text({"t": "input", "ev": "mouse_down", "button": button})
    await asyncio.sleep(0.15)
    await session.send_text({"t": "input", "ev": "mouse_up", "button": button})
    print(f"sent {button} click at {x},{y}", flush=True)
    await asyncio.sleep(2.0)
    await session.close()
    await client.close()

asyncio.run(main())
