"""真机取一张画面：主控端连上 session、收到第一帧就存成图片，用来肉眼确认画面内容。

用法：python probe_frame.py [输出路径，默认 %TEMP%\\desk_frame.jpg] [最多等待秒数，默认 12]
被控端：ETHANDESK_AGENT_ID / ETHANDESK_SESSION_CODE；口令 ETHANDESK_RELAY_PASSWORD。
"""

from __future__ import annotations

import asyncio
import os
import sys
import time

from viewer.client import DeskClient

PASSWORD = os.environ.get("ETHANDESK_RELAY_PASSWORD", "")
URL = os.environ.get("ETHANDESK_VIEWER_URL", "https://desk.prasinospower.com")


async def main(path: str, wait_seconds: float) -> None:
    agent_id = os.environ.get("ETHANDESK_AGENT_ID", "")
    code = os.environ.get("ETHANDESK_SESSION_CODE", "")
    if not agent_id:
        print("RESULT failed 缺少 ETHANDESK_AGENT_ID", flush=True)
        raise SystemExit(2)
    started = time.time()
    async with DeskClient(URL, password=PASSWORD) as client:
        await client.login()
        session = await client.open_session(agent_id, code)
        await session.send_text({"t": "frame_recv"})
        while time.time() - started < wait_seconds:
            kind, payload = await session.receive()
            if kind == "closed":
                print("RESULT failed 会话已断开", flush=True)
                return
            if kind == "text" and isinstance(payload, dict) and payload.get("t") == "bin":
                width = payload.get("width")
                height = payload.get("height")
                size = payload.get("size")
                header = payload
                continue
            if kind == "bin" and isinstance(payload, bytes):
                with open(path, "wb") as handle:
                    handle.write(payload)
                print(f"saved {path} ({len(payload)} bytes) "
                      f"header={header.get('width')}x{header.get('height')} "
                      f"declared_size={header.get('size')}", flush=True)
                print("RESULT ok", flush=True)
                return
        print("RESULT failed 没收到画面", flush=True)


if __name__ == "__main__":
    target = sys.argv[1] if len(sys.argv) > 1 else os.path.join(
        os.environ.get("TEMP", "."), "desk_frame.jpg"
    )
    limit = float(sys.argv[2]) if len(sys.argv) > 2 else 12.0
    asyncio.run(main(target, limit))
