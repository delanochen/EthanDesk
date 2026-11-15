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

    async def open_session(self, agent_id: str, code: str):
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


