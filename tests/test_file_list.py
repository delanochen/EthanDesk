"""远程文件浏览：主控端列被控端目录，被控端用 meta/file_list 回条目。"""

from __future__ import annotations

import asyncio
import os
import queue

from agent.app import AgentWorker, AgentConfig
from viewer.app import ViewerWorker, format_size


class _FakeWs:
    def __init__(self) -> None:
        self.closed = False
        self.texts: list[dict] = []

    async def send_str(self, data) -> None:
        import json

        self.texts.append(json.loads(data))

    async def send_bytes(self, data) -> None:
        return None


def _agent() -> AgentWorker:
    worker = AgentWorker(AgentConfig(), queue.Queue(), queue.Queue())
    worker._connected = True
    worker._active_session = "sid-1"
    return worker


def test_list_directory_sorts_directories_first(tmp_path):
    (tmp_path / "zeta.txt").write_bytes(b"1234")
    (tmp_path / "alpha.txt").write_bytes(b"1")
    (tmp_path / "sub").mkdir()
    (tmp_path / "Another").mkdir()

    entries, directory, error = _agent().list_directory(str(tmp_path))

    assert error == ""
    assert directory == str(tmp_path)
    names = [entry["name"] for entry in entries]
    assert names[:2] == ["Another", "sub"]
    assert entries[0]["dir"] is True and entries[0]["size"] == 0
    sizes = {entry["name"]: entry["size"] for entry in entries if not entry["dir"]}
    assert sizes["zeta.txt"] == 4


def test_list_directory_accepts_quoted_path(tmp_path):
    entries, directory, error = _agent().list_directory(f'"{tmp_path}"')
    assert error == ""
    assert directory == str(tmp_path)
    assert entries == []


def test_list_directory_rejects_relative_and_missing(tmp_path):
    worker = _agent()
    assert worker.list_directory("Documents") == ([], "", "只接受绝对路径：Documents")
    entries, _directory, error = worker.list_directory(str(tmp_path / "nope"))
    assert entries == []
    assert "目录不存在" in error


def test_list_directory_caps_the_entry_count(tmp_path, monkeypatch):
    import agent.app as agent_app

    monkeypatch.setattr(agent_app, "MAX_LIST_ENTRIES", 3)
    for index in range(10):
        (tmp_path / f"f{index}.txt").write_bytes(b"x")
    entries, _directory, error = _agent().list_directory(str(tmp_path))
    assert error == ""
    assert len(entries) == 3


def test_list_directory_returns_drive_roots_for_empty_path():
    worker = _agent()
    entries, directory, error = worker.list_directory("")
    if os.name == "nt":
        assert directory == ""
        assert error == ""
        assert entries and all(entry["dir"] for entry in entries)
        assert entries[0]["name"].endswith(":\\")
    else:
        assert entries == []


def test_serve_list_replies_with_meta_file_list(tmp_path):
    (tmp_path / "file.txt").write_bytes(b"abc")
    worker = _agent()
    ws = _FakeWs()

    asyncio.run(worker._serve_list(ws, {"path": str(tmp_path)}))

    reply = ws.texts[-1]
    assert reply["t"] == "meta"
    assert reply["kind"] == "file_list"
    assert reply["path"] == str(tmp_path)
    assert reply["error"] == ""
    assert [entry["name"] for entry in reply["entries"]] == ["file.txt"]


def test_serve_list_reports_the_error_in_the_reply(tmp_path):
    worker = _agent()
    ws = _FakeWs()

    asyncio.run(worker._serve_list(ws, {"path": str(tmp_path / "gone")}))

    reply = ws.texts[-1]
    assert reply["kind"] == "file_list"
    assert "目录不存在" in reply["error"]


def test_serve_list_needs_an_active_session(tmp_path):
    worker = _agent()
    worker._active_session = ""
    ws = _FakeWs()

    asyncio.run(worker._serve_list(ws, {"path": str(tmp_path)}))

    assert [item for item in ws.texts if item.get("kind") == "file_list"] == []
    logs = [item.get("text", "") for item in ws.texts if item.get("t") == "log"]
    assert any("没有活动会话" in text for text in logs)


def test_list_request_on_the_text_path_reaches_the_lister(tmp_path):
    worker = _agent()
    ws = _FakeWs()

    asyncio.run(
        worker._handle_text(
            ws, {"t": "file_offer", "direction": "list", "path": str(tmp_path)}
        )
    )

    assert ws.texts[-1]["kind"] == "file_list"


class _StubSession:
    def __init__(self) -> None:
        self.sent: list[dict] = []

    async def send_text(self, message) -> None:
        self.sent.append(message)


def test_viewer_requests_a_listing_through_the_known_offer_type():
    worker = ViewerWorker(queue.Queue(), queue.Queue())
    session = _StubSession()

    async def run() -> None:
        worker._session = session  # type: ignore[assignment]
        await worker._request_list(r"C:\Users\admin\Desktop")

    asyncio.run(run())

    assert session.sent == [
        {"t": "file_offer", "direction": "list", "path": r"C:\Users\admin\Desktop"}
    ]
    assert worker._last_remote_path == r"C:\Users\admin\Desktop"


def test_viewer_surfaces_file_list_metas_as_remote_list_events():
    worker = ViewerWorker(queue.Queue(), queue.Queue())

    async def run() -> None:
        await worker._handle_text(
            {"t": "meta", "kind": "file_list", "path": "C:\\", "entries": [], "error": ""}
        )

    asyncio.run(run())

    events = []
    while not worker.events.empty():
        events.append(worker.events.get())
    kinds = [kind for kind, _payload in events]
    assert "remote_list" in kinds
    assert "meta" not in kinds


def test_plain_meta_still_goes_to_the_picture_path():
    worker = ViewerWorker(queue.Queue(), queue.Queue())

    async def run() -> None:
        await worker._handle_text({"t": "meta", "width": 1920, "height": 1080})

    asyncio.run(run())

    events = []
    while not worker.events.empty():
        events.append(worker.events.get())
    assert [kind for kind, _payload in events] == ["meta"]


def test_format_size_is_readable():
    assert format_size(0) == "0 B"
    assert format_size(900) == "900 B"
    assert format_size(1536) == "1.5 KB"
    assert format_size(5 * 1024 * 1024) == "5.0 MB"
