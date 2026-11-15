"""本机主控端探针：走公开中继连接指定设备，按给定参数统计收帧。"""

from __future__ import annotations

import asyncio
import os
import sys
import time

sys.path.insert(0, r"C:\Users\Administrator\Projects\EthanDesk")

from viewer.client import DeskClient, DeskClientError

BASE = "https://desk.prasinospower.com"
# 口令不进仓库：跑之前设 ETHANDESK_RELAY_PASSWORD。
PASSWORD = os.environ.get("ETHANDESK_RELAY_PASSWORD", "")
AGENT_ID = os.environ.get("ETHANDESK_AGENT_ID", "448098688")
CODE = os.environ.get("ETHANDESK_SESSION_CODE", "246810")


async def main() -> None:
    seconds = float(sys.argv[1]) if len(sys.argv) > 1 else 30.0
    scale = float(sys.argv[2]) if len(sys.argv) > 2 else 0.4
    quality = int(sys.argv[3]) if len(sys.argv) > 3 else 45

    client = DeskClient(BASE)
    try:
        await client.login(PASSWORD)
        session = await client.open_session(AGENT_ID, CODE)
        await session.send_text({"t": "set_quality", "scale": scale, "quality": quality, "fps": 10})
        frames = 0
        total = 0
        first = None
        last = None
        deadline = time.time() + seconds
        while time.time() < deadline:
            try:
                kind, payload = await asyncio.wait_for(
                    session.receive(), timeout=max(0.2, min(2.0, deadline - time.time()))
                )
            except asyncio.TimeoutError:
                continue
            if kind == "closed":
                print("closed by relay", flush=True)
                break
            if kind == "text":
                if isinstance(payload, dict):
                    print(f"[text] {payload}", flush=True)
                continue
            now = time.time()
            frames += 1
            total += len(payload)
            first = first or now
            last = now
            await session.send_text({"t": "frame_recv"})
        await session.close()
        if frames:
            elapsed = (last - first) if last > first else 0.0
            fps = (frames - 1) / elapsed if elapsed > 0 else 0.0
            print(f"RESULT frames={frames} elapsed={elapsed:.1f}s fps={fps:.2f} avg={total // frames}B", flush=True)
        else:
            print("RESULT frames=0", flush=True)
    except DeskClientError as exc:
        print("RESULT error:", exc, flush=True)
    finally:
        await client.close()


if __name__ == "__main__":
    asyncio.run(main())
