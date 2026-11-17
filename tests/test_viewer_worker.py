"""Regression tests for the viewer worker command dispatch."""

from __future__ import annotations

import asyncio
import queue

from viewer.app import ViewerWorker, next_video_profile


def _worker() -> ViewerWorker:
    worker = ViewerWorker(queue.Queue(), queue.Queue())
    worker._client = object()  # pretend we are already logged in
    return worker


def test_connect_command_passes_agent_id_and_code():
    worker = _worker()
    seen: dict = {}

    async def fake_connect(payload):
        seen["payload"] = payload

    worker._connect = fake_connect
    asyncio.run(worker._dispatch(("connect", {"agent_id": "146896644", "code": "246810"})))

    assert seen["payload"] == {"agent_id": "146896644", "code": "246810"}


def test_connect_command_still_accepts_a_bare_id():
    worker = _worker()
    seen: dict = {}

    async def fake_connect(payload):
        seen["payload"] = payload

    worker._connect = fake_connect
    asyncio.run(worker._dispatch(("connect", "146896644")))

    assert seen["payload"] == "146896644"


class _FakeSession:
    """Minimal session stub: records outgoing text and closes immediately."""

    def __init__(self) -> None:
        self.sent: list[dict] = []

    async def send_text(self, message: dict) -> None:
        self.sent.append(message)

    async def receive(self):
        return ("closed", None)

    async def close(self) -> None:
        pass


class _FakeClient:
    def __init__(self) -> None:
        self.session = _FakeSession()
        self.opened: list[tuple[str, str]] = []

    async def open_session(
        self, agent_id: str, code: str, *, login_user: str = "", login_password: str = ""
    ):
        self.opened.append((agent_id, code))
        return self.session


def test_settings_are_cached_before_a_session_exists():
    worker = _worker()
    asyncio.run(worker._dispatch(("settings", {"quality": 45, "scale": 0.6, "fps": 8})))

    assert worker._video == {"quality": 45, "scale": 0.6, "fps": 8}


def test_connect_pushes_cached_video_settings_to_the_agent():
    worker = ViewerWorker(queue.Queue(), queue.Queue())
    worker._client = _FakeClient()
    worker._remember_settings({"quality": 55, "scale": 0.75, "fps": 10})

    asyncio.run(worker._connect({"agent_id": "146896644", "code": "246810"}))

    assert worker._client.session.sent == [
        {"t": "set_quality", "quality": 55, "scale": 0.75, "fps": 10}
    ]


def test_auto_quality_shrinks_the_picture_when_the_link_is_slow():
    updated = next_video_profile({"quality": 70, "scale": 1.0, "fps": 15}, fps=1.2)

    assert updated is not None
    assert updated["scale"] == 0.85
    assert updated["quality"] == 62


def test_auto_quality_stops_shrinking_at_the_floor():
    profile = {"quality": 38, "scale": 0.5, "fps": 15}

    assert next_video_profile(profile, fps=0.5) is None


def test_auto_quality_does_not_react_to_a_healthy_link():
    assert next_video_profile({"quality": 70, "scale": 1.0, "fps": 15}, fps=9.0) is None


def test_manual_settings_take_precedence_over_auto_quality():
    worker = ViewerWorker(queue.Queue(), queue.Queue())
    asyncio.run(worker._dispatch(("settings", {"quality": 60, "scale": 0.8, "fps": 10})))

    assert worker._auto_quality is False


def test_credentials_travel_with_the_connect_command_and_the_reconnect():
    worker = ViewerWorker(queue.Queue(), queue.Queue())
    client = _FakeClient()
    worker._client = client
    seen: list[dict] = []

    async def fake_open(agent_id, code, login_user="", login_password=""):
        seen.append({"agent_id": agent_id, "user": login_user, "password": login_password})
        return _FakeSession()

    client.open_session = fake_open
    asyncio.run(worker._dispatch(("connect", {
        "agent_id": "146896644", "code": "1", "user": "Guest", "password": "pw",
    })))

    assert seen == [{"agent_id": "146896644", "user": "Guest", "password": "pw"}]
    # 掉线重连必须沿用同一份凭据，否则被控端会立刻拒绝。
    assert worker._reconnect_user == "Guest"
    assert worker._reconnect_password == "pw"


def test_a_login_refusal_surfaces_as_need_login_not_a_generic_error():
    from viewer.client import SessionDenied

    worker = ViewerWorker(queue.Queue(), queue.Queue())
    events: list[tuple[str, object]] = []

    def fake_open(agent_id, code, login_user="", login_password=""):
        raise SessionDenied("denied", code="bad_credentials", need_login=True)

    worker._client = _FakeClient()
    worker._client.open_session = fake_open
    worker._emit = lambda kind, payload=None: events.append((kind, payload))
    asyncio.run(worker._dispatch(("connect", {"agent_id": "146896644"})))

    assert events[-1][0] == "need_login"
    assert events[-1][1]["code"] == "bad_credentials"


def test_open_session_sends_credentials_as_ascii_headers():
    from common import credentials as creds
    from viewer.client import DeskClient

    captured: dict = {}

    class _Ws:
        async def receive(self):
            from types import SimpleNamespace

            import aiohttp

            from common.protocol import encode

            return SimpleNamespace(
                type=aiohttp.WSMsgType.TEXT,
                data=encode({"t": "session_accept", "sid": "s1"}),
            )

        async def close(self):
            pass

    class _Session:
        async def ws_connect(self, url, **kwargs):
            captured["url"] = url
            captured["headers"] = kwargs.get("headers")
            return _Ws()

    async def scenario():
        # 绕过构造函数：它会真的开一个 aiohttp.ClientSession，测试里用不上。
        client = DeskClient.__new__(DeskClient)
        client.base_url = "https://example.com"
        client.token = "tkn"
        client._session = _Session()
        return await client.open_session(
            "146896644", login_user="Guest", login_password="p@ss"
        )

    session = asyncio.run(scenario())
    headers = captured["headers"]
    assert headers[creds.HEADER_USER] == creds.encode("Guest")
    assert headers[creds.HEADER_PASSWORD] == creds.encode("p@ss")
    # 凭据不能出现在 URL 里：那会被反代的访问日志记下来。
    assert "p@ss" not in captured["url"] and "Guest" not in captured["url"]
    assert session is not None



