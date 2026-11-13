"""The relay server: presence API, viewer login and the session bridge.

Two websocket endpoints share one process:

``/ws/agent``    Windows agents authenticate with ``agent_id`` + key, then keep the
                 connection open with heartbeats.
``/ws/session``  Authenticated viewers ask the relay to bridge them to one agent.

Once a session is accepted by the local operator, every text and binary message is
forwarded verbatim in both directions; the relay only counts frames for the audit log.
"""

from __future__ import annotations

import asyncio
import json
import secrets
import time
import uuid
from typing import Any

from aiohttp import WSMsgType, web

from common.auth import AuthError, issue_token, verify_token
from common.protocol import ProtocolError, decode, encode
from relay.config import RelayConfig
from relay.registry import Registry, SessionLog


class AgentLink:
    """Per-agent connection plus, when active, the viewer attached to it."""

    def __init__(self, agent_id: str, ws: web.WebSocketResponse) -> None:
        self.agent_id = agent_id
        self.ws = ws
        self.viewer_ws: web.WebSocketResponse | None = None
        self.viewer_name = ""
        self.session_id: str | None = None
        self.frames = 0
        self.active = False
        self._pending_sid: str | None = None
        self._future: asyncio.Future | None = None

    @property
    def busy(self) -> bool:
        return self.viewer_ws is not None

    def start_session(
        self,
        viewer_ws: web.WebSocketResponse,
        viewer_name: str,
        session_id: str,
        future: asyncio.Future,
    ) -> None:
        self.viewer_ws = viewer_ws
        self.viewer_name = viewer_name
        self.session_id = session_id
        self.frames = 0
        self.active = False
        self._pending_sid = session_id
        self._future = future

    def mark_active(self) -> None:
        """Forwarding only starts once the viewer has received ``session_accept``."""
        self.active = True

    def resolve(self, message: dict[str, Any]) -> bool:
        """Complete the pending consent request when the sid matches."""
        if self._future is None or message.get("sid") != self._pending_sid:
            return False
        if not self._future.done():
            self._future.set_result("accept" if message.get("t") == "session_accept" else "deny")
        return True

    def detach(self) -> None:
        self.viewer_ws = None
        self.viewer_name = ""
        self.session_id = None
        self.frames = 0
        self.active = False
        self._pending_sid = None
        self._future = None


CONFIG_KEY: web.AppKey = web.AppKey("config", RelayConfig)
REGISTRY_KEY: web.AppKey = web.AppKey("registry", Registry)
SESSIONS_KEY: web.AppKey = web.AppKey("sessions", SessionLog)
LINKS_KEY: web.AppKey = web.AppKey("links", dict)
MONITOR_KEY: web.AppKey = web.AppKey("monitor_task", asyncio.Task)


def _bearer(request: web.Request) -> str:
    header = request.headers.get("Authorization", "")
    if header.lower().startswith("bearer "):
        return header[7:].strip()
    return request.query.get("token", "")


def _authorise(request: web.Request) -> dict[str, Any]:
    config: RelayConfig = request.app[CONFIG_KEY]
    token = _bearer(request)
    try:
        return verify_token(config.agent_key + ":viewer", token)
    except AuthError as exc:
        raise web.HTTPUnauthorized(text=json.dumps({"error": str(exc)}), content_type="application/json")


async def handle_health(request: web.Request) -> web.Response:
    registry: Registry = request.app[REGISTRY_KEY]
    return web.json_response({"ok": True, "agents": len(registry.list_agents()), "time": time.time()})


async def handle_login(request: web.Request) -> web.Response:
    config: RelayConfig = request.app[CONFIG_KEY]
    try:
        body = await request.json()
    except (json.JSONDecodeError, ValueError):
        raise web.HTTPBadRequest(text=json.dumps({"error": "invalid json"}), content_type="application/json")

    password = str(body.get("password", ""))
    if not secrets.compare_digest(password, config.viewer_password):
        raise web.HTTPUnauthorized(text=json.dumps({"error": "bad password"}), content_type="application/json")

    token = issue_token(config.agent_key + ":viewer", "viewer", ttl=config.token_ttl)
    return web.json_response({"token": token, "expires_in": config.token_ttl})


async def handle_agents(request: web.Request) -> web.Response:
    _authorise(request)
    registry: Registry = request.app[REGISTRY_KEY]
    return web.json_response({"agents": registry.list_agents()})


async def handle_sessions(request: web.Request) -> web.Response:
    _authorise(request)
    log: SessionLog = request.app[SESSIONS_KEY]
    return web.json_response({"sessions": log.recent()})


async def _send(ws: web.WebSocketResponse | None, message: dict[str, Any]) -> bool:
    if ws is None or ws.closed:
        return False
    try:
        await ws.send_str(encode(message))
        return True
    except (ConnectionResetError, RuntimeError):
        return False


async def handle_agent_ws(request: web.Request) -> web.WebSocketResponse:
    config: RelayConfig = request.app[CONFIG_KEY]
    registry: Registry = request.app[REGISTRY_KEY]
    links: dict[str, AgentLink] = request.app[LINKS_KEY]

    ws = web.WebSocketResponse(heartbeat=20, max_msg_size=0, autoping=True)
    await ws.prepare(request)

    try:
        first = await asyncio.wait_for(ws.receive(), 30)
    except asyncio.TimeoutError:
        await ws.close()
        return ws

    if first.type != WSMsgType.TEXT:
        await ws.close()
        return ws

    try:
        hello = decode(first.data)
    except ProtocolError:
        hello = None
    if not hello or hello.get("t") != "hello":
        await _send(ws, {"t": "error", "code": "bad_hello"})
        await ws.close()
        return ws

    agent_id = str(hello.get("agent_id", "")).strip()
    key = str(hello.get("key", ""))
    if not agent_id or not secrets.compare_digest(config.key_for(agent_id), key):
        await _send(ws, {"t": "error", "code": "bad_agent_key"})
        await ws.close()
        return ws

    rejection = links.get(agent_id)
    if rejection is not None and not rejection.busy:
        pass  # previous connection for the same id is replaced

    info = hello.get("info") or {}
    registry.register(
        agent_id,
        name=str(info.get("name") or agent_id),
        host=str(info.get("host", "")),
        os_name=str(info.get("os", "")),
        version=str(info.get("version", "")),
    )
    link = AgentLink(agent_id, ws)
    links[agent_id] = link
    await _send(ws, {"t": "hello_ok", "server_time": time.time()})

    try:
        async for message in ws:
            if message.type == WSMsgType.TEXT:
                try:
                    decoded = decode(message.data)
                except ProtocolError:
                    continue
                if decoded is None:
                    continue
                registry.heartbeat(agent_id)
                kind = decoded.get("t")
                if kind == "ping":
                    await _send(ws, {"t": "pong", "server_time": time.time()})
                    continue
                if kind in ("session_accept", "session_denied"):
                    link.resolve(decoded)
                    continue
                if not link.active:
                    continue
                await _send(link.viewer_ws, decoded)
            elif message.type == WSMsgType.BINARY:
                if not link.active:
                    continue
                link.frames += 1
                viewer = link.viewer_ws
                if viewer is not None and not viewer.closed:
                    try:
                        await viewer.send_bytes(message.data)
                    except (ConnectionResetError, RuntimeError):
                        pass
            elif message.type in (WSMsgType.CLOSE, WSMsgType.ERROR):
                break
    finally:
        if link.busy:
            await _send(link.viewer_ws, {"t": "error", "code": "agent_gone"})
            viewer = link.viewer_ws
            if viewer is not None and not viewer.closed:
                await viewer.close()
        links.pop(agent_id, None)
        registry.unregister(agent_id)
        link.detach()

    return ws


async def handle_viewer_ws(request: web.Request) -> web.WebSocketResponse:
    config: RelayConfig = request.app[CONFIG_KEY]
    registry: Registry = request.app[REGISTRY_KEY]
    sessions: SessionLog = request.app[SESSIONS_KEY]
    links: dict[str, AgentLink] = request.app[LINKS_KEY]

    token = _bearer(request)
    try:
        payload = verify_token(config.agent_key + ":viewer", token)
    except AuthError:
        raise web.HTTPUnauthorized(text=json.dumps({"error": "bad token"}), content_type="application/json")

    agent_id = request.query.get("agent_id", "")
    viewer_name = str(payload.get("sub", "viewer"))

    ws = web.WebSocketResponse(heartbeat=20, max_msg_size=0, autoping=True)
    await ws.prepare(request)

    link = links.get(agent_id)
    if link is None or not registry.is_online(agent_id):
        await _send(ws, {"t": "error", "code": "agent_offline", "agent_id": agent_id})
        await ws.close()
        return ws

    if link.busy:
        await _send(ws, {"t": "error", "code": "agent_busy", "agent_id": agent_id})
        await ws.close()
        return ws

    session_id = uuid.uuid4().hex[:12]
    future: asyncio.Future = asyncio.get_running_loop().create_future()
    link.start_session(ws, viewer_name, session_id, future)

    if not await _send(link.ws, {"t": "session_request", "sid": session_id, "viewer": viewer_name}):
        link.detach()
        await _send(ws, {"t": "error", "code": "agent_offline", "agent_id": agent_id})
        await ws.close()
        return ws

    try:
        decision = await asyncio.wait_for(future, config.session_accept_timeout)
    except asyncio.TimeoutError:
        link.detach()
        await _send(ws, {"t": "error", "code": "session_timeout"})
        await ws.close()
        return ws

    if decision != "accept":
        link.detach()
        await _send(ws, {"t": "error", "code": "session_denied"})
        await ws.close()
        return ws

    await _send(ws, {"t": "session_accept", "sid": session_id})
    link.mark_active()
    await _send(link.ws, {"t": "session_start", "sid": session_id})
    registry.set_session(agent_id, session_id)
    sessions.record_open(session_id, agent_id, viewer_name, time.time())

    try:
        async for message in ws:
            if message.type == WSMsgType.TEXT:
                try:
                    decoded = decode(message.data)
                except ProtocolError:
                    continue
                if decoded is None:
                    continue
                if decoded.get("t") == "ping":
                    await _send(ws, {"t": "pong"})
                    continue
                await _send(link.ws, decoded)
            elif message.type == WSMsgType.BINARY:
                if not link.ws.closed:
                    try:
                        await link.ws.send_bytes(message.data)
                    except (ConnectionResetError, RuntimeError):
                        pass
            elif message.type in (WSMsgType.CLOSE, WSMsgType.ERROR):
                break
    finally:
        sessions.record_close(session_id, time.time(), link.frames)
        registry.set_session(agent_id, None)
        await _send(link.ws, {"t": "session_close", "sid": session_id})
        link.detach()
        if not ws.closed:
            await ws.close()

    return ws


async def _presence_monitor(app: web.Application) -> None:
    registry: Registry = app[REGISTRY_KEY]
    config: RelayConfig = app[CONFIG_KEY]
    interval = max(5.0, config.heartbeat_timeout / 3)
    try:
        while True:
            registry.refresh_online()
            await asyncio.sleep(interval)
    except asyncio.CancelledError:
        raise


async def _start_monitor(app: web.Application) -> None:
    app[MONITOR_KEY] = asyncio.create_task(_presence_monitor(app))


async def _stop_monitor(app: web.Application) -> None:
    task = app.get(MONITOR_KEY)
    if task is not None:
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass


async def _close_sessions(app: web.Application) -> None:
    log: SessionLog | None = app.get(SESSIONS_KEY)
    if log is not None:
        log.close()


def create_app(
    config: RelayConfig | None = None,
    *,
    registry: Registry | None = None,
    session_log: SessionLog | None = None,
) -> web.Application:
    config = config or RelayConfig.load()
    app = web.Application()
    app[CONFIG_KEY] = config
    app[REGISTRY_KEY] = registry or Registry(timeout=config.heartbeat_timeout)
    app[SESSIONS_KEY] = session_log or SessionLog(config.db_path)
    app[LINKS_KEY] = {}

    app.router.add_get("/api/health", handle_health)
    app.router.add_post("/api/login", handle_login)
    app.router.add_get("/api/agents", handle_agents)
    app.router.add_get("/api/sessions", handle_sessions)
    app.router.add_get("/ws/agent", handle_agent_ws)
    app.router.add_get("/ws/session", handle_viewer_ws)

    app.on_startup.append(_start_monitor)
    app.on_cleanup.append(_stop_monitor)
    app.on_cleanup.append(_close_sessions)
    return app


def main() -> None:
    config = RelayConfig.load()
    issues = config.problems()
    if issues:
        for issue in issues:
            print(f"[warn] {issue}")
    web.run_app(create_app(config), host=config.host, port=config.port, print=None)


if __name__ == "__main__":
    main()
