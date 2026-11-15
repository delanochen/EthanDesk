"""The relay must forward the viewer-supplied session code to the agent."""

import asyncio
import json

from aiohttp import ClientSession, web

from relay.app import create_app
from relay.config import RelayConfig

WS_TIMEOUT = 5.0


async def _boot(config: RelayConfig):
    app = create_app(config)
    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, "127.0.0.1", 0)
    await site.start()
    port = site._server.sockets[0].getsockname()[1]
    return runner, f"http://127.0.0.1:{port}"


def test_session_request_carries_viewer_code(tmp_path):
    async def scenario():
        config = RelayConfig(viewer_password="pw", agent_key="ak", db_path=str(tmp_path / "r.db"))
        runner, base = await _boot(config)
        ws_base = base.replace("http://", "ws://")
        try:
            async with ClientSession() as session:
                login = await session.post(f"{base}/api/login", json={"password": "pw"})
                token = (await login.json())["token"]

                agent_ws = await session.ws_connect(ws_base + "/ws/agent")
                await agent_ws.send_str(
                    json.dumps(
                        {
                            "t": "hello",
                            "agent_id": "a1",
                            "key": "ak",
                            "info": {
                                "name": "Office-PC",
                                "host": "office-pc",
                                "os": "Windows",
                                "version": "1.0",
                            },
                        }
                    )
                )
                await asyncio.wait_for(agent_ws.receive_str(), WS_TIMEOUT)

                viewer = await session.ws_connect(
                    f"{ws_base}/ws/session?agent_id=a1&token={token}&code=123456"
                )
                request = json.loads(await asyncio.wait_for(agent_ws.receive_str(), WS_TIMEOUT))
                assert request["t"] == "session_request"
                assert request["code"] == "123456"

                await agent_ws.send_str(json.dumps({"t": "session_denied", "sid": request["sid"]}))
                await asyncio.wait_for(viewer.receive_str(), WS_TIMEOUT)
                await viewer.close()
                await agent_ws.close()
        finally:
            await runner.cleanup()

    asyncio.run(scenario())
