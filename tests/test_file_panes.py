"""双栏远程文件：上传落到远端当前目录、本地栏扫描、上一级导航。"""

from __future__ import annotations

import asyncio
import os
import queue

from agent.app import AgentConfig, AgentWorker
from common.listing import list_directory, parent_of


def _agent(tmp_path) -> AgentWorker:
    config = AgentConfig()
    config.download_dir = str(tmp_path / "default")
    os.makedirs(config.download_dir, exist_ok=True)
    worker = AgentWorker(config, queue.Queue(), queue.Queue())
    worker._connected = True
    worker._active_session = "sid"
    return worker


def test_up_offer_without_dir_lands_in_download_dir(tmp_path):
    worker = _agent(tmp_path)
    refusal = worker._open_incoming(
        {"t": "file_offer", "direction": "up", "id": "a1", "name": "x.txt", "size": 3}
    )
    assert refusal == ""
    incoming = worker._incoming["a1"]
    assert incoming.target_dir == worker.config.download_dir


def test_up_offer_with_valid_dir_lands_there(tmp_path):
    worker = _agent(tmp_path)
    target = tmp_path / "chosen"
    target.mkdir()
    refusal = worker._open_incoming(
        {"t": "file_offer", "direction": "up", "id": "a2", "name": "y.txt",
         "size": 3, "dir": f'"{target}"'}
    )
    assert refusal == ""
    assert worker._incoming["a2"].target_dir == str(target)


def test_up_offer_with_bogus_dir_is_refused(tmp_path):
    worker = _agent(tmp_path)
    missing = str(tmp_path / "nope")
    refusal = worker._open_incoming(
        {"t": "file_offer", "direction": "up", "id": "a3", "name": "z.txt",
         "size": 3, "dir": missing}
    )
    assert "目标目录无效" in refusal
    assert "a3" not in worker._incoming


def test_up_offer_with_relative_dir_is_refused(tmp_path):
    worker = _agent(tmp_path)
    refusal = worker._open_incoming(
        {"t": "file_offer", "direction": "up", "id": "a4", "name": "z.txt",
         "size": 3, "dir": "chosen"}
    )
    assert "目标目录无效" in refusal


class _FakeWs:
    def __init__(self) -> None:
        self.closed = False
        self.texts: list[dict] = []

    async def send_str(self, data) -> None:
        import json

        self.texts.append(json.loads(data))


def test_bad_upload_dir_is_reported_to_the_viewer(tmp_path):
    worker = _agent(tmp_path)
    ws = _FakeWs()

    asyncio.run(
        worker._handle_text(
            ws,
            {"t": "file_offer", "direction": "up", "id": "a5", "name": "z.txt",
             "size": 3, "dir": str(tmp_path / "missing")},
        )
    )

    logs = [item.get("text", "") for item in ws.texts if item.get("t") == "log"]
    assert any("目标目录无效" in text for text in logs)


class _StubSession:
    def __init__(self) -> None:
        self.sent: list[dict] = []

    async def send_text(self, message) -> None:
        self.sent.append(dict(message))

    async def send_bin(self, header, chunk) -> None:
        return None


def test_viewer_upload_carries_the_remote_directory(tmp_path):
    from viewer.app import ViewerWorker

    local = tmp_path / "hello.txt"
    local.write_bytes(b"hello")
    worker = ViewerWorker(queue.Queue(), queue.Queue())
    session = _StubSession()

    async def run() -> None:
        worker._session = session  # type: ignore[assignment]
        await worker._upload(str(local), r"C:\Users\admin\Desktop")

    asyncio.run(run())

    offers = [item for item in session.sent if item.get("t") == "file_offer"]
    assert offers and offers[0]["direction"] == "up"
    assert offers[0]["dir"] == r"C:\Users\admin\Desktop"
    assert offers[0]["name"] == "hello.txt"


def test_viewer_upload_without_dir_stays_backwards_compatible(tmp_path):
    from viewer.app import ViewerWorker

    local = tmp_path / "plain.txt"
    local.write_bytes(b"x")
    worker = ViewerWorker(queue.Queue(), queue.Queue())
    session = _StubSession()

    async def run() -> None:
        worker._session = session  # type: ignore[assignment]
        await worker._upload(str(local))

    asyncio.run(run())

    offers = [item for item in session.sent if item.get("t") == "file_offer"]
    assert offers and "dir" not in offers[0]


def test_listing_is_shared_between_local_and_remote(tmp_path):
    (tmp_path / "b.txt").write_bytes(b"12")
    (tmp_path / "A").mkdir()
    entries, directory, error = list_directory(str(tmp_path), 500)
    assert error == "" and directory == str(tmp_path)
    assert entries[0]["name"] == "A" and entries[0]["dir"] is True


def test_listing_respects_the_limit(tmp_path):
    for index in range(6):
        (tmp_path / f"f{index}.txt").write_bytes(b"")
    entries, _directory, _error = list_directory(str(tmp_path), 4)
    assert len(entries) == 4


def test_parent_of_walks_up_and_stops_at_drive_root():
    assert parent_of(r"C:\Users\admin\Desktop") == r"C:\Users\admin"
    assert parent_of(r"C:\Users") == "C:" + "\\"
    assert parent_of("C:\\") == ""
    assert parent_of("") == ""
