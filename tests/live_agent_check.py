"""Live check: real agent capture loop -> local relay -> DeskClient.

Requires an interactive Windows desktop (it grabs the screen). It never injects mouse or
keyboard events, so it is safe to run on a machine someone is using.

    python -m tests.live_agent_check
"""

from __future__ import annotations

import asyncio
import queue
import sys
import tempfile

from aiohttp import web

from agent.app import AgentWorker
from agent.config import AgentConfig
from relay.app import create_app
from relay.config import RelayConfig
from viewer.client import DeskClient

PORT = 18770
FRAMES_WANTED = 5


def _drain_once(worker: AgentWorker, events: "queue.Queue", online: asyncio.Event) -> None:
    while True:
        try:
            kind, payload = events.get_nowait()
        except queue.Empty:
            return
        if kind == "consent_request":
            worker.answer_consent(str(payload["sid"]), True)
        elif kind == "status" and payload == "online":
            online.set()


async def _consent_loop(
    worker: AgentWorker, events: "queue.Queue", online: asyncio.Event
) -> None:
    while True:
        _drain_once(worker, events, online)
        await asyncio.sleep(0.05)


async def _run() -> int:
    with tempfile.TemporaryDirectory() as tmp:
        config = RelayConfig(
            viewer_password="live-password",
            agent_key="live-agent-key-0123456789",
            db_path=f"{tmp}/relay.db",
        )
        app = create_app(config)
        runner = web.AppRunner(app)
        await runner.setup()
        await web.TCPSite(runner, "127.0.0.1", PORT).start()

        events: "queue.Queue" = queue.Queue()
        commands: "queue.Queue" = queue.Queue()
        agent_config = AgentConfig(
            relay_url=f"ws://127.0.0.1:{PORT}/ws/agent",
            agent_id="live01",
            agent_key=config.agent_key,
            name="Live-Check",
            fps=8,
            quality=60,
            scale=0.5,
        )
        worker = AgentWorker(agent_config, events, commands)
        worker.start()

        online = asyncio.Event()
        consent_task = asyncio.create_task(_consent_loop(worker, events, online))
        client = None
        try:
            wait_deadline = asyncio.get_running_loop().time() + 20
            while not online.is_set():
                if asyncio.get_running_loop().time() > wait_deadline:
                    raise TimeoutError("agent never reached the relay")
                await asyncio.sleep(0.05)

            client = DeskClient(f"http://127.0.0.1:{PORT}", password="live-password")
            await client.login()
            agents = await client.list_agents()
            assert any(agent["id"] == "live01" for agent in agents), agents

            session = await client.open_session("live01")
            frames = 0
            meta = None
            while frames < FRAMES_WANTED:
                kind, payload = await asyncio.wait_for(session.receive(), 10)
                if kind == "closed":
                    break
                if kind == "text" and isinstance(payload, dict):
                    if payload.get("t") == "meta":
                        meta = payload
                    elif payload.get("t") == "error":
                        raise RuntimeError(f"relay error: {payload.get('code')}")
                    continue
                if kind == "bin":
                    frames += 1
                    await session.send_text({"t": "frame_recv"})

            await session.close()
            await asyncio.sleep(0.2)
            sessions = await client.list_sessions()
            assert frames >= FRAMES_WANTED, f"only {frames} frames arrived"
            assert meta and meta.get("width", 0) > 0, f"missing meta: {meta}"
            print(
                f"live ok: frames={frames} desktop={meta['width']}x{meta['height']} "
                f"logged={sessions[0]['frames'] if sessions else 0}"
            )
            return 0
        finally:
            consent_task.cancel()
            if client is not None:
                await client.close()
            worker.stop()
            await runner.cleanup()


def main() -> int:
    return asyncio.run(_run())


if __name__ == "__main__":
    sys.exit(main())
