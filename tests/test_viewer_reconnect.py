"""主控端断线重连：非用户主动断开的会话结束必须自愈，用户的「断开」不许自愈。"""

from __future__ import annotations

import asyncio
import queue

import viewer.app as viewer_app
from viewer.app import ViewerWorker
from viewer.client import DeskClientError


class _ClosedSession:
    async def receive(self):
        return ("closed", None)

    async def send_text(self, message) -> None:
        return None

    async def close(self) -> None:
        return None


def _worker() -> ViewerWorker:
    return ViewerWorker(queue.Queue(), queue.Queue())


def _drain(worker: ViewerWorker) -> list[tuple[str, object]]:
    items = []
    while not worker.events.empty():
        items.append(worker.events.get())
    return items


def test_should_reconnect_after_a_dropped_session():
    worker = _worker()
    worker._reconnect_agent = "146896644"
    assert worker._should_reconnect() is True


def test_manual_disconnect_stops_reconnecting():
    worker = _worker()
    worker._reconnect_agent = "146896644"
    worker._manual_disconnect = True
    assert worker._should_reconnect() is False


def test_stopping_stops_reconnecting():
    worker = _worker()
    worker._reconnect_agent = "146896644"
    worker._stopping = True
    assert worker._should_reconnect() is False


def test_closed_session_schedules_reconnect():
    worker = _worker()
    worker._reconnect_agent = "146896644"
    scheduled: list[str] = []
    worker._schedule_reconnect = lambda: scheduled.append("yes")

    asyncio.run(worker._receive_loop(_ClosedSession()))

    assert scheduled == ["yes"]
    assert worker._session is None
    assert "session_closed" in [kind for kind, _ in _drain(worker)]


def test_user_disconnect_does_not_schedule_reconnect():
    worker = _worker()
    worker._reconnect_agent = "146896644"
    worker._manual_disconnect = True
    scheduled: list[str] = []
    worker._schedule_reconnect = lambda: scheduled.append("yes")

    asyncio.run(worker._receive_loop(_ClosedSession()))

    assert scheduled == []


def test_reconnect_loop_retries_until_the_session_opens(monkeypatch):
    """被控端还在重新上线时开会话会被拒，隔一会儿再试，直到成功。"""
    monkeypatch.setattr(viewer_app, "VIEWER_RECONNECT_DELAY", 0.01)
    monkeypatch.setattr(viewer_app, "VIEWER_RECONNECT_MAX_DELAY", 0.02)
    worker = _worker()
    worker._reconnect_agent = "146896644"
    worker._reconnect_code = "864209"
    attempts: list[dict] = []

    async def fake_connect(payload) -> None:
        attempts.append(payload)
        if len(attempts) < 3:
            raise DeskClientError("会话被拒绝：agent_offline")

    worker._connect = fake_connect

    asyncio.run(worker._reconnect_loop())

    assert [item["agent_id"] for item in attempts] == ["146896644", "146896644", "146896644"]
    assert attempts[0]["code"] == "864209"
    logs = [str(payload) for kind, payload in _drain(worker) if kind in ("log", "status")]
    assert any("已自动重连" in entry for entry in logs)
