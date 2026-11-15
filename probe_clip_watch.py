"""反向剪贴板验收：主控端连上后，被控端本机复制的内容要能回传到主控端。

用法：python probe_clip_watch.py [秒数，默认 25]
被控端：ETHANDESK_AGENT_ID / ETHANDESK_SESSION_CODE；口令 ETHANDESK_RELAY_PASSWORD。
输出每个 ``clip`` 事件的 dir 与内容，最后打印统计。
"""

from __future__ import annotations

import asyncio
import os
import sys
import time

from common.defaults import DEFAULT_VIEWER_URL
from viewer.client import DeskClient

PASSWORD = os.environ.get("ETHANDESK_RELAY_PASSWORD", "")


async def main() -> None:
    seconds = float(sys.argv[1]) if len(sys.argv) > 1 else 25.0
    agent_id = os.environ.get("ETHANDESK_AGENT_ID", "")
    code = os.environ.get("ETHANDESK_SESSION_CODE", "")
    if not agent_id:
        print("RESULT failed 缺少 ETHANDESK_AGENT_ID", flush=True)
        raise SystemExit(2)

    client = DeskClient(DEFAULT_VIEWER_URL, password=PASSWORD)
    session = None
    received = 0
    try:
        await client.login()
        session = await client.open_session(agent_id, code)
        print("watch start", flush=True)
        deadline = time.monotonic() + seconds
        while time.monotonic() < deadline:
            try:
                kind, payload = await asyncio.wait_for(session.receive(), timeout=1.0)
            except asyncio.TimeoutError:
                continue
            if kind == "closed":
                print("session closed", flush=True)
                break
            if kind == "text" and isinstance(payload, dict) and payload.get("t") == "clip":
                received += 1
                print(
                    f"CLIP dir={payload.get('dir')} text={str(payload.get('text'))[:80]!r}",
                    flush=True,
                )
        print(f"RESULT clips={received}", flush=True)
    except Exception as exc:
        print(f"RESULT failed {exc!r}", flush=True)
    finally:
        if session is not None:
            await session.close()
        await client.close()


asyncio.run(main())
