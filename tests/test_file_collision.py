"""同名文件不许静默覆盖：落下时自动改名 ``name (1).ext``，源文件保留。"""

from __future__ import annotations

import os

from agent.files_transfer import IncomingFile
from common.files import unique_path


def _touch(path: str, payload: bytes = b"old") -> str:
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "wb") as handle:
        handle.write(payload)
    return path


def _receive(directory: str, name: str, payload: bytes) -> str:
    incoming = IncomingFile(
        "t1",
        name,
        len(payload),
        directory,
        __import__("hashlib").sha256(payload).hexdigest(),
    )
    incoming.feed(0, payload)
    return incoming.finish()


def test_unique_path_keeps_the_original_when_free(tmp_path):
    assert unique_path(str(tmp_path), "report.pdf") == os.path.join(str(tmp_path), "report.pdf")


def test_unique_path_adds_a_counter_before_the_suffix(tmp_path):
    _touch(os.path.join(str(tmp_path), "report.pdf"))
    assert unique_path(str(tmp_path), "report.pdf") == os.path.join(
        str(tmp_path), "report (1).pdf"
    )


def test_unique_path_walks_up_until_it_finds_a_gap(tmp_path):
    for suffix in ("report.pdf", "report (1).pdf", "report (2).pdf"):
        _touch(os.path.join(str(tmp_path), suffix))
    assert unique_path(str(tmp_path), "report.pdf") == os.path.join(
        str(tmp_path), "report (3).pdf"
    )


def test_unique_path_handles_names_without_a_suffix(tmp_path):
    _touch(os.path.join(str(tmp_path), "README"))
    assert unique_path(str(tmp_path), "README") == os.path.join(str(tmp_path), "README (1)")


def test_incoming_file_does_not_overwrite_an_existing_file(tmp_path):
    target = _touch(os.path.join(str(tmp_path), "notes.txt"), b"keep me")
    saved = _receive(str(tmp_path), "notes.txt", b"new payload")

    assert saved != target
    assert os.path.basename(saved) == "notes (1).txt"
    with open(target, "rb") as handle:
        assert handle.read() == b"keep me"
    with open(saved, "rb") as handle:
        assert handle.read() == b"new payload"


def test_incoming_file_is_unique_per_repeated_upload(tmp_path):
    first = _receive(str(tmp_path), "frame.bin", b"one")
    second = _receive(str(tmp_path), "frame.bin", b"two")
    third = _receive(str(tmp_path), "frame.bin", b"three")

    assert {os.path.basename(item) for item in (first, second, third)} == {
        "frame.bin",
        "frame (1).bin",
        "frame (2).bin",
    }
    with open(first, "rb") as handle:
        assert handle.read() == b"one"
