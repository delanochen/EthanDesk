"""End-to-end smoke: relay + fake agent + real viewer client, no GUI, no desktop.

Run with ``python -m tests.smoke_e2e`` (exit code 0 means the loop closed).
"""

from __future__ import annotations

import asyncio
import json
import sys
import tempfile
import time

import aiohttp
from aiohttp import web

from agent.screen import encode_image
from common.protocol import encode, make_bin_header
from relay.app import create_app
from relay.config import RelayConfig
from viewer.client import DeskClient

FRAMES = 3


async def _boot(config: RelayConfig, port: int):
    app = create_app(config)
    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, "127.0.0.1", port)
    await site.start()
    return runner


async def _fake_agent(base_ws: str, key: str, frame_payloads: list[bytes], ready: asyncio.Event) -> None:
    async with aiohttp.ClientSession() as session:
        async with session.ws_connect(f"{base_ws}/ws/agent", heartbeat=30, max_msg_size=0) as ws:
            await ws.send_str(
                encode(
                    {
                        "t": "hello",
                        "agent_id": "smoke01",
                        "key": key,
                        "info": {"name": "Smoke-PC", "host": "smoke", "os": "Windows", "version": "1.0"},
                    }
                )
            )
            ready.set()
            async for message in ws:
                if message.type != aiohttp.WSMsgType.TEXT:
                    continue
                decoded = json.loads(message.data)
                if decoded.get("t") == "session_request":
                    await ws.send_str(encode({"t": "session_accept", "sid": decoded["sid"]}))
                elif decoded.get("t") == "session_start":
                    for index, payload in enumerate(frame_payloads):
                        await ws.send_str(
                            encode(make_bin_header("frame", seq=index, size=len(payload), width=64, height=36))
                        )
                        await ws.send_bytes(payload)
                        await asyncio.sleep(0.05)
                elif decoded.get("t") == "session_close":
                    break


async def _run() -> int:
    from PIL import Image

    frame_payloads = [encode_image(Image.new("RGB", (64, 36), (index * 40, 60, 90)))[0] for index in range(FRAMES)]
    port = 18765
    with tempfile.TemporaryDirectory() as tmp:
        config = RelayConfig(
            viewer_password="smoke-password",
            agent_key="smoke-agent-key-0123456789",
            db_path=f"{tmp}/relay.db",
            heartbeat_timeout=30.0,
        )
        runner = await _boot(config, port)
        base = f"http://127.0.0.1:{port}"
        base_ws = f"ws://127.0.0.1:{port}"
        ready = asyncio.Event()
        agent_task = asyncio.create_task(_fake_agent(base_ws, config.agent_key, frame_payloads, ready))
        try:
            await asyncio.wait_for(ready.wait(), 5)
            client = DeskClient(base, password="smoke-password")
            await client.login()
            agents = await client.list_agents()
            assert [agent["id"] for agent in agents] == ["smoke01"], agents

            session = await client.open_session("smoke01")
            frames = 0
            deadline = time.time() + 10
            while frames < FRAMES and time.time() < deadline:
                kind, payload = await asyncio.wait_for(session.receive(), 5)
                if kind == "bin":
                    frames += 1
                    await session.send_text({"t": "frame_recv"})
                elif kind == "closed":
                    break
            await session.close()
            sessions = await client.list_sessions()
            await client.close()

            assert frames == FRAMES, f"expected {FRAMES} frames, got {frames}"
            assert sessions and sessions[0]["frames"] >= FRAMES, sessions
            print(f"smoke ok: frames={frames} sessions={len(sessions)} log_frames={sessions[0]['frames']}")
            return 0
        finally:
            agent_task.cancel()
            await runner.cleanup()


def main() -> int:
    return asyncio.run(_run())


if __name__ == "__main__":
    sys.exit(main())
