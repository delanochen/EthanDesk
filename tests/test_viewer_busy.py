"""刚连上被控端时撞上 agent_busy：自动等它收尾再试，而不是让用户手动点第二次。

agent_busy 是「被控端/中继还没把上一个会话收尾」，通常几秒内就释放。
真实场景：用户刚断开（或上一个主控端进程刚被杀），马上又想连进去。
"""

from __future__ import annotations

import asyncio
import queue

import viewer.app as viewer_app
from viewer.app import ViewerWorker
from viewer.client import DeskClientError


class _Session:
    async def send_text(self, message) -> None:
        return None

    async def receive(self):
        return ("closed", None)

    async def close(self) -> None:
        return None


class _Client:
    """按脚本依次返回结果：抛出异常，或给出一个会话。"""

    def __init__(self, script: list[object]) -> None:
        self.script = script
        self.calls: list[tuple[str, str]] = []

    async def open_session(self, agent_id: str, code: str = "") -> _Session:
        self.calls.append((agent_id, code))
        item = self.script[min(len(self.calls) - 1, len(self.script) - 1)]
        if isinstance(item, Exception):
            raise item
        return item


def _worker() -> ViewerWorker:
    return ViewerWorker(queue.Queue(), queue.Queue())


def _drain(worker: ViewerWorker) -> list[tuple[str, object]]:
    items = []
    while not worker.events.empty():
        items.append(worker.events.get())
    return items


def _busy() -> DeskClientError:
    return DeskClientError("会话被拒绝：agent_busy", code="agent_busy")


def test_desk_client_error_keeps_the_relay_code():
    error = DeskClientError("会话被拒绝：agent_busy", code="agent_busy")
    assert error.code == "agent_busy"
    assert str(error) == "会话被拒绝：agent_busy"


def test_plain_error_has_no_code():
    assert DeskClientError("登录口令不正确").code == ""


def test_open_session_waits_out_agent_busy(monkeypatch):
    monkeypatch.setattr(viewer_app, "VIEWER_BUSY_RETRY_DELAY", 0.01)
    worker = _worker()
    client = _Client([_busy(), _busy(), _Session()])
    worker._client = client

    session = asyncio.run(worker._open_session("146896644", "864209"))

    assert isinstance(session, _Session)
    assert client.calls == [("146896644", "864209")] * 3
    statuses = [str(payload) for kind, payload in _drain(worker) if kind == "status"]
    assert statuses and "重试" in statuses[0]
    assert "被控端" in statuses[0]


def test_open_session_gives_up_after_the_retry_budget(monkeypatch):
    monkeypatch.setattr(viewer_app, "VIEWER_BUSY_RETRY_DELAY", 0.01)
    monkeypatch.setattr(viewer_app, "VIEWER_BUSY_RETRIES", 2)
    worker = _worker()
    client = _Client([_busy()])
    worker._client = client

    try:
        asyncio.run(worker._open_session("146896644", ""))
    except DeskClientError as exc:
        assert getattr(exc, "code", "") == "agent_busy"
    else:
        raise AssertionError("agent_busy 不该无限重试")

    assert len(client.calls) == 3


def test_open_session_does_not_retry_other_refusals(monkeypatch):
    """别的拒绝（口令错、被控端离线、主控端被拒）要立刻报，别拖成几十秒。"""
    monkeypatch.setattr(viewer_app, "VIEWER_BUSY_RETRY_DELAY", 0.01)
    worker = _worker()
    client = _Client([DeskClientError("会话被拒绝：agent_offline", code="agent_offline")])
    worker._client = client

    try:
        asyncio.run(worker._open_session("146896644", ""))
    except DeskClientError as exc:
        assert getattr(exc, "code", "") == "agent_offline"
    else:
        raise AssertionError("agent_offline 应直接抛出")

    assert len(client.calls) == 1


def test_connect_uses_the_busy_retry(monkeypatch):
    monkeypatch.setattr(viewer_app, "VIEWER_BUSY_RETRY_DELAY", 0.01)
    worker = _worker()
    client = _Client([_busy(), _Session()])
    worker._client = client
    worker._schedule_reconnect = lambda: None

    asyncio.run(worker._connect({"agent_id": "146896644", "code": "864209"}))

    assert client.calls == [("146896644", "864209")] * 2
    assert "session_open" in [kind for kind, _ in _drain(worker)]
