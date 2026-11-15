"""远端取文件：主控端发 file_get，被控端校验路径后以 direction="down" 回传整份文件。"""

from __future__ import annotations

import asyncio
import hashlib
import os
import queue

import agent.app as agent_app
from agent.app import AgentWorker, AgentConfig
from viewer.app import ViewerWorker


class _FakeWs:
    def __init__(self) -> None:
        self.closed = False
        self.texts: list[dict] = []
        self.binaries: list[bytes] = []

    async def send_str(self, data) -> None:
        import json

        self.texts.append(json.loads(data))

    async def send_bytes(self, data) -> None:
        self.binaries.append(data)


def _worker() -> AgentWorker:
    worker = AgentWorker(AgentConfig(), queue.Queue(), queue.Queue())
    worker._connected = True
    return worker


def _events(worker: AgentWorker) -> list[tuple[str, object]]:
    items = []
    while not worker.events.empty():
        items.append(worker.events.get())
    return items


def _serve(worker: AgentWorker, ws: _FakeWs, path: str) -> None:
    asyncio.run(worker._serve_get(ws, {"t": "file_get", "path": path}))


def test_file_get_streams_the_file_with_a_down_offer(tmp_path):
    payload = b"ethandesk-file-get" * 120_000  # >1 MiB，跨分块
    source = tmp_path / "报告.txt"
    source.write_bytes(payload)
    worker = _worker()
    worker._active_session = "sid-1"
    ws = _FakeWs()

    _serve(worker, ws, str(source))

    offer = ws.texts[0]
    assert offer["t"] == "file_offer"
    assert offer["direction"] == "down"
    assert offer["name"] == "报告.txt"
    assert offer["size"] == len(payload)
    assert offer["sha256"] == hashlib.sha256(payload).hexdigest()
    headers = [item for item in ws.texts[1:] if item.get("t") == "bin"]
    assert len(headers) == len(ws.binaries) == len(list(agent_app.OutgoingFile(
        offer["id"], "报告.txt", payload).frames()))
    joined = b"".join(ws.binaries)
    assert hashlib.sha256(joined).hexdigest() == offer["sha256"]
    logs = [str(payload) for kind, payload in _events(worker) if kind == "log"]
    assert any("已发送文件" in entry for entry in logs)


def test_file_get_needs_an_active_session(tmp_path):
    source = tmp_path / "a.txt"
    source.write_bytes(b"abc")
    worker = _worker()
    ws = _FakeWs()

    _serve(worker, ws, str(source))

    assert [item for item in ws.texts if item.get("t") == "file_offer"] == []
    logs = [str(payload) for kind, payload in _events(worker) if kind == "log"]
    assert any("没有活动会话" in entry for entry in logs)


def test_relative_path_is_refused():
    worker = _worker()
    assert worker.resolve_pull_path("Documents/report.txt")[0] is None


def test_missing_file_is_refused(tmp_path):
    worker = _worker()
    path, reason = worker.resolve_pull_path(str(tmp_path / "missing.txt"))
    assert path is None
    assert "不存在" in reason


def test_directory_is_refused_with_a_clear_reason(tmp_path):
    worker = _worker()
    path, reason = worker.resolve_pull_path(str(tmp_path))
    assert path is None
    assert "目录" in reason


def test_oversized_file_is_refused(tmp_path, monkeypatch):
    monkeypatch.setattr(agent_app, "MAX_PULL_BYTES", 16)
    source = tmp_path / "big.bin"
    source.write_bytes(b"x" * 64)
    worker = _worker()
    path, reason = worker.resolve_pull_path(str(source))
    assert path is None
    assert "太大" in reason


def test_quoted_path_is_accepted(tmp_path):
    source = tmp_path / "quoted.txt"
    source.write_bytes(b"abc")
    worker = _worker()
    path, reason = worker.resolve_pull_path(f'"{source}"')
    assert path == str(source)
    assert reason == ""


def test_refusal_is_reported_back_to_the_viewer(tmp_path):
    """拒绝原因必须回给主控端，否则用户点了下载只会看到没反应。"""
    worker = _worker()
    worker._active_session = "sid-1"
    ws = _FakeWs()

    _serve(worker, ws, str(tmp_path))

    notices = [item.get("text", "") for item in ws.texts if item.get("t") == "log"]
    assert any("远端取文件被拒绝" in text and "目录" in text for text in notices)


def test_success_is_reported_back_to_the_viewer(tmp_path):
    source = tmp_path / "ok.txt"
    source.write_bytes(b"ok" * 100)
    worker = _worker()
    worker._active_session = "sid-1"
    ws = _FakeWs()

    _serve(worker, ws, str(source))

    notices = [item.get("text", "") for item in ws.texts if item.get("t") == "log"]
    assert any("已发送文件 ok.txt" in text for text in notices)


def test_get_offer_on_the_text_path_streams_the_file(tmp_path):
    """线上中继丢未知类型，所以请求走 file_offer/direction="get"，这条路径必须能用。"""
    source = tmp_path / "via-offer.txt"
    source.write_bytes(b"carrier" * 1000)
    worker = _worker()
    worker._active_session = "sid-1"
    ws = _FakeWs()

    asyncio.run(
        worker._handle_text(
            ws, {"t": "file_offer", "direction": "get", "path": str(source)}
        )
    )

    assert ws.texts[0]["t"] == "file_offer"
    assert ws.texts[0]["direction"] == "down"
    assert ws.texts[0]["name"] == "via-offer.txt"


def test_incoming_up_offer_still_goes_to_the_receiver(tmp_path):
    worker = _worker()
    worker.config.download_dir = str(tmp_path)
    asyncio.run(
        worker._handle_text(
            _FakeWs(),
            {
                "t": "file_offer",
                "direction": "up",
                "id": "abc",
                "name": "up.txt",
                "size": 3,
            },
        )
    )
    assert "abc" in worker._incoming


def test_file_get_survives_the_wire_protocol():
    """file_get 必须在 KNOWN_TYPES 里，否则 decode 直接返 None，消息发出去就没了。"""
    from common.protocol import decode, encode

    message = {"t": "file_get", "path": r"C:\Users\admin\Desktop\报告.docx"}
    assert decode(encode(message)) == message


class _StubSession:
    def __init__(self) -> None:
        self.sent: list[dict] = []

    async def send_text(self, message) -> None:
        self.sent.append(message)


def test_viewer_requests_a_download_through_the_known_offer_type():
    worker = ViewerWorker(queue.Queue(), queue.Queue())
    session = _StubSession()

    async def run() -> None:
        worker._session = session  # type: ignore[assignment]
        await worker._request_download(r"C:\Users\admin\Desktop\报告.docx")

    asyncio.run(run())

    assert session.sent == [
        {
            "t": "file_offer",
            "direction": "get",
            "path": r"C:\Users\admin\Desktop\报告.docx",
        }
    ]
    logs = [str(payload) for kind, payload in _drain(worker) if kind == "log"]
    assert any("已请求下载" in entry for entry in logs)


def test_viewer_refuses_empty_remote_path():
    worker = ViewerWorker(queue.Queue(), queue.Queue())
    session = _StubSession()

    async def run() -> None:
        worker._session = session  # type: ignore[assignment]
        await worker._request_download("   ")

    asyncio.run(run())

    assert session.sent == []
    errors = [str(payload) for kind, payload in _drain(worker) if kind == "error"]
    assert any("远程路径为空" in entry for entry in errors)


def _drain(worker: ViewerWorker) -> list[tuple[str, object]]:
    items = []
    while not worker.events.empty():
        items.append(worker.events.get())
    return items
