import asyncio
import json

import pytest
from aiohttp import ClientSession, WSMsgType, web
from aiohttp.client_exceptions import WSServerHandshakeError

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


async def _login(session: ClientSession, base: str, password: str) -> str:
    response = await session.post(f"{base}/api/login", json={{"password": password}})
    assert response.status == 200
    return (await response.json())["token"]


async def _connect_agent(session: ClientSession, base: str, agent_id: str, key: str):
    ws = await session.ws_connect(base.replace("http://", "ws://") + "/ws/agent")
    await ws.send_str(
        json.dumps(
            {{
                "t": "hello",
                "agent_id": agent_id,
                "key": key,
                "info": {{"name": "Office-PC", "host": "office-pc", "os": "Windows", "version": "1.0"}},
            }}
        )
    )
    reply = json.loads(await asyncio.wait_for(ws.receive_str(), WS_TIMEOUT))
    assert reply["t"] == "hello_ok"
    return ws


def test_login_rejects_wrong_password(tmp_path):
    async def scenario():
        config = RelayConfig(viewer_password="pw", agent_key="ak", db_path=str(tmp_path / "r.db"))
        runner, base = await _boot(config)
        try:
            async with ClientSession() as session:
                response = await session.post(f"{base}/api/login", json={{"password": "nope"}})
                assert response.status == 401
        finally:
            await runner.cleanup()

    asyncio.run(scenario())


def test_agents_api_requires_token(tmp_path):
    async def scenario():
        config = RelayConfig(viewer_password="pw", agent_key="ak", db_path=str(tmp_path / "r.db"))
        runner, base = await _boot(config)
        try:
            async with ClientSession() as session:
                response = await session.get(f"{base}/api/agents")
                assert response.status == 401
        finally:
            await runner.cleanup()

    asyncio.run(scenario())


def test_agent_registration_and_listing(tmp_path):
    async def scenario():
        config = RelayConfig(viewer_password="pw", agent_key="ak", db_path=str(tmp_path / "r.db"))
        runner, base = await _boot(config)
        try:
            async with ClientSession() as session:
                token = await _login(session, base, "pw")
                ws = await _connect_agent(session, base, "a1", "ak")
                response = await session.get(
                    f"{base}/api/agents", headers={{"Authorization": f"Bearer {{token}}"}}
                )
                agents = (await response.json())["agents"]
                assert [agent["id"] for agent in agents] == ["a1"]
                assert agents[0]["online"] is True
                assert agents[0]["name"] == "Office-PC"
                await ws.close()
        finally:
            await runner.cleanup()

    asyncio.run(scenario())


def test_agent_rejects_bad_key(tmp_path):
    async def scenario():
        config = RelayConfig(viewer_password="pw", agent_key="ak", db_path=str(tmp_path / "r.db"))
        runner, base = await _boot(config)
        try:
            async with ClientSession() as session:
                ws = await session.ws_connect(base.replace("http://", "ws://") + "/ws/agent")
                await ws.send_str(json.dumps({{"t": "hello", "agent_id": "a1", "key": "bad"}}))
                reply = json.loads(await asyncio.wait_for(ws.receive_str(), WS_TIMEOUT))
                assert reply["t"] == "error"
                assert reply["code"] == "bad_agent_key"
        finally:
            await runner.cleanup()

    asyncio.run(scenario())


def test_session_bridges_text_and_binary(tmp_path):
    async def scenario():
        config = RelayConfig(viewer_password="pw", agent_key="ak", db_path=str(tmp_path / "r.db"))
        runner, base = await _boot(config)
        try:
            async with ClientSession() as session:
                token = await _login(session, base, "pw")
                agent_ws = await _connect_agent(session, base, "a1", "ak")
                viewer = await session.ws_connect(
                    f"{{base.replace('http://', 'ws://')}}/ws/session?agent_id=a1&token={{token}}"
                )

                request = json.loads(await asyncio.wait_for(agent_ws.receive_str(), WS_TIMEOUT))
                assert request["t"] == "session_request"
                assert request["sid"]
                await agent_ws.send_str(json.dumps({{"t": "session_accept", "sid": request["sid"]}}))

                accepted = json.loads(await asyncio.wait_for(viewer.receive_str(), WS_TIMEOUT))
                assert accepted["t"] == "session_accept"

                start = json.loads(await asyncio.wait_for(agent_ws.receive_str(), WS_TIMEOUT))
                assert start["t"] == "session_start"

                await viewer.send_str(json.dumps({{"t": "input", "ev": "mouse_move", "x": 0.5, "y": 0.5}}))
                forwarded = json.loads(await asyncio.wait_for(agent_ws.receive_str(), WS_TIMEOUT))
                assert forwarded["t"] == "input"
                assert forwarded["ev"] == "mouse_move"

                await agent_ws.send_str(json.dumps({{"t": "bin", "kind": "frame", "seq": 1, "size": 3}}))
                await agent_ws.send_bytes(b"abc")
                header = json.loads(await asyncio.wait_for(viewer.receive_str(), WS_TIMEOUT))
                assert header["t"] == "bin"
                frame = await asyncio.wait_for(viewer.receive(), WS_TIMEOUT)
                assert frame.type == WSMsgType.BINARY
                assert frame.data == b"abc"

                await viewer.close()
                closed = json.loads(await asyncio.wait_for(agent_ws.receive_str(), WS_TIMEOUT))
                assert closed["t"] == "session_close"
                await agent_ws.close()
        finally:
            await runner.cleanup()

    asyncio.run(scenario())


def test_session_denied_by_agent(tmp_path):
    async def scenario():
        config = RelayConfig(viewer_password="pw", agent_key="ak", db_path=str(tmp_path / "r.db"))
        runner, base = await _boot(config)
        try:
            async with ClientSession() as session:
                token = await _login(session, base, "pw")
                agent_ws = await _connect_agent(session, base, "a1", "ak")
                viewer = await session.ws_connect(
                    f"{{base.replace('http://', 'ws://')}}/ws/session?agent_id=a1&token={{token}}"
                )
                request = json.loads(await asyncio.wait_for(agent_ws.receive_str(), WS_TIMEOUT))
                await agent_ws.send_str(json.dumps({{"t": "session_denied", "sid": request["sid"]}}))
                reply = json.loads(await asyncio.wait_for(viewer.receive_str(), WS_TIMEOUT))
                assert reply["t"] == "error"
                assert reply["code"] == "session_denied"
                await viewer.close()
                await agent_ws.close()
        finally:
            await runner.cleanup()

    asyncio.run(scenario())


def test_session_reports_offline_agent(tmp_path):
    async def scenario():
        config = RelayConfig(viewer_password="pw", agent_key="ak", db_path=str(tmp_path / "r.db"))
        runner, base = await _boot(config)
        try:
            async with ClientSession() as session:
                token = await _login(session, base, "pw")
                viewer = await session.ws_connect(
                    f"{{base.replace('http://', 'ws://')}}/ws/session?agent_id=ghost&token={{token}}"
                )
                reply = json.loads(await asyncio.wait_for(viewer.receive_str(), WS_TIMEOUT))
                assert reply["t"] == "error"
                assert reply["code"] == "agent_offline"
                await viewer.close()
        finally:
            await runner.cleanup()

    asyncio.run(scenario())


def test_session_rejects_bad_token(tmp_path):
    async def scenario():
        config = RelayConfig(viewer_password="pw", agent_key="ak", db_path=str(tmp_path / "r.db"))
        runner, base = await _boot(config)
        try:
            async with ClientSession() as session:
                with pytest.raises(WSServerHandshakeError):
                    await session.ws_connect(
                        f"{{base.replace('http://', 'ws://')}}/ws/session?agent_id=a1&token=tampered"
                    )
        finally:
            await runner.cleanup()

    asyncio.run(scenario())


def test_session_log_persists_on_close(tmp_path):
    async def scenario():
        config = RelayConfig(viewer_password="pw", agent_key="ak", db_path=str(tmp_path / "r.db"))
        runner, base = await _boot(config)
        try:
            async with ClientSession() as session:
                token = await _login(session, base, "pw")
                agent_ws = await _connect_agent(session, base, "a1", "ak")
                viewer = await session.ws_connect(
                    f"{{base.replace('http://', 'ws://')}}/ws/session?agent_id=a1&token={{token}}"
                )
                request = json.loads(await asyncio.wait_for(agent_ws.receive_str(), WS_TIMEOUT))
                await agent_ws.send_str(json.dumps({{"t": "session_accept", "sid": request["sid"]}}))
                await asyncio.wait_for(viewer.receive_str(), WS_TIMEOUT)
                await agent_ws.send_str(json.dumps({{"t": "bin", "kind": "frame", "seq": 1, "size": 3}}))
                await agent_ws.send_bytes(b"abc")
                await asyncio.wait_for(viewer.receive(), WS_TIMEOUT)
                await viewer.close()
                await asyncio.sleep(0.2)

                rows = await (
                    await session.get(
                        f"{{base}}/api/sessions", headers={{"Authorization": f"Bearer {{token}}"}}
                    )
                ).json()
                sessions = rows["sessions"]
                assert len(sessions) == 1
                assert sessions[0]["frames"] >= 1
                assert sessions[0]["ended_at"] is not None
                await agent_ws.close()
        finally:
            await runner.cleanup()

    asyncio.run(scenario())
