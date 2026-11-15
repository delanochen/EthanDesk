"""从远控会话点击一个归一化坐标，用来验证点击落地。

慢链路和中继的偶发 reset 都会让一次连接失败，所以这里重试几次；每次失败都把
真实原因打出来，而不是只留一句“Unclosed client session”。

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
ATTEMPTS = 3


async def attempt(x: float, y: float, button: str) -> bool:
    config = AgentConfig.load()
    client = DeskClient(DEFAULT_VIEWER_URL)
    try:
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
        return True
    except Exception as exc:  # 中继 reset / 会话超时都要看得见
        print(f"attempt failed: {exc!r}", flush=True)
        return False
    finally:
        await client.close()


async def main() -> None:
    x = float(sys.argv[1]) if len(sys.argv) > 1 else 0.47
    y = float(sys.argv[2]) if len(sys.argv) > 2 else 0.49
    button = sys.argv[3] if len(sys.argv) > 3 else "left"

    for index in range(1, ATTEMPTS + 1):
        if await attempt(x, y, button):
            print(f"RESULT ok attempt={index}", flush=True)
            return
        await asyncio.sleep(2.0)
    print("RESULT failed", flush=True)


asyncio.run(main())
