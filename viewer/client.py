"""Async client used by the viewer GUI (and by the end-to-end smoke test)."""

from __future__ import annotations

import asyncio
from typing import Any

import aiohttp

from common.protocol import ProtocolError, decode, encode


class DeskClientError(Exception):
    """Raised for authentication or transport failures the GUI should surface."""


def to_http_base(base_url: str) -> str:
    base = base_url.strip().rstrip("/")
    if base.startswith("wss://"):
        return "https://" + base[len("wss://") :]
    if base.startswith("ws://"):
        return "http://" + base[len("ws://") :]
    if base.startswith(("http://", "https://")):
        return base
    return "https://" + base


def to_ws_base(base_url: str) -> str:
    base = to_http_base(base_url)
    if base.startswith("https://"):
        return "wss://" + base[len("https://") :]
    return "ws://" + base[len("http://") :]


class Session:
    """One bridged viewer↔agent websocket."""

    def __init__(self, ws: aiohttp.ClientWebSocketResponse) -> None:
        self.ws = ws

    async def send_text(self, payload: dict[str, Any]) -> None:
        if not self.ws.closed:
            await self.ws.send_str(encode(payload))

    async def send_bin(self, header: dict[str, Any], data: bytes) -> None:
        if not self.ws.closed:
            await self.ws.send_str(encode(header))
            await self.ws.send_bytes(data)

    async def receive(self) -> tuple[str, Any]:
        """Return ``("text", dict)``, ``("bin", bytes)`` or ``("closed", None)``."""
        message = await self.ws.receive()
        if message.type == aiohttp.WSMsgType.TEXT:
            try:
                decoded = decode(message.data)
            except ProtocolError:
                return ("text", None)
            return ("text", decoded)
        if message.type == aiohttp.WSMsgType.BINARY:
            return ("bin", message.data)
        if message.type in (aiohttp.WSMsgType.CLOSE, aiohttp.WSMsgType.CLOSING, aiohttp.WSMsgType.ERROR):
            return ("closed", None)
        if message.type == aiohttp.WSMsgType.CLOSED:
            return ("closed", None)
        return ("text", None)

    async def close(self) -> None:
        if not self.ws.closed:
            await self.ws.close()


class DeskClient:
    """Thin HTTP/WebSocket wrapper around the relay API."""

    def __init__(
        self,
        base_url: str,
        *,
        password: str = "",
        token: str = "",
        verify_ssl: bool = True,
        timeout: float = 15.0,
    ) -> None:
        self.base_url = to_http_base(base_url)
        self.password = password
        self.token = token
        self._session = aiohttp.ClientSession(
            timeout=aiohttp.ClientTimeout(total=timeout),
            connector=aiohttp.TCPConnector(ssl=None if verify_ssl else False),
        )

    @property
    def _auth_headers(self) -> dict[str, str]:
        return {"Authorization": f"Bearer {self.token}"} if self.token else {}

    async def login(self, password: str | None = None) -> str:
        secret = self.password if password is None else password
        try:
            response = await self._session.post(
                f"{self.base_url}/api/login", json={"password": secret}
            )
        except aiohttp.ClientError as exc:
            raise DeskClientError(f"无法连接中继：{exc}") from exc
        if response.status == 401:
            raise DeskClientError("登录口令不正确")
        if response.status != 200:
            raise DeskClientError(f"登录失败：HTTP {response.status}")
        payload = await response.json()
        self.token = str(payload.get("token", ""))
        self.password = secret
        return self.token

    async def list_agents(self) -> list[dict[str, Any]]:
        try:
            response = await self._session.get(f"{self.base_url}/api/agents", headers=self._auth_headers)
        except aiohttp.ClientError as exc:
            raise DeskClientError(f"无法连接中继：{exc}") from exc
        if response.status != 200:
            raise DeskClientError(f"读取设备列表失败：HTTP {response.status}")
        return list((await response.json()).get("agents", []))

    async def list_sessions(self, limit: int = 50) -> list[dict[str, Any]]:
        response = await self._session.get(
            f"{self.base_url}/api/sessions", headers=self._auth_headers, params={"limit": limit}
        )
        if response.status != 200:
            raise DeskClientError(f"读取会话记录失败：HTTP {response.status}")
        return list((await response.json()).get("sessions", []))

    async def open_session(self, agent_id: str) -> Session:
        if not self.token:
            await self.login()
        url = (
            f"{to_ws_base(self.base_url)}/ws/session?agent_id={agent_id}&token={self.token}"
        )
        try:
            ws = await self._session.ws_connect(url, heartbeat=30, max_msg_size=0)
        except aiohttp.ClientError as exc:
            raise DeskClientError(f"建立会话失败：{exc}") from exc

        session = Session(ws)
        message_type, payload = await session.receive()
        if message_type == "text" and isinstance(payload, dict):
            if payload.get("t") == "session_accept":
                return session
            code = payload.get("code", "unknown")
            await session.close()
            raise DeskClientError(f"会话被拒绝：{code}")
        await session.close()
        raise DeskClientError("会话握手未完成")

    async def close(self) -> None:
        await self._session.close()

    async def __aenter__(self) -> "DeskClient":
        return self

    async def __aexit__(self, *exc_info) -> None:
        await self.close()
