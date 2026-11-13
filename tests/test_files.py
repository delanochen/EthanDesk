import hashlib

import pytest

from common.files import (
    CHUNK_SIZE,
    FileTransferError,
    Reassembler,
    iter_chunks,
    safe_target_name,
    sha256_bytes,
)


def test_chunk_count_matches_ceil():
    data = b"x" * (CHUNK_SIZE * 2 + 10)
    chunks = list(iter_chunks(data))
    assert len(chunks) == 3
    assert chunks[0][0] == 0
    assert chunks[-1][1] == b"x" * 10


def test_chunks_are_ordered_and_contiguous():
    data = bytes(range(256)) * 20
    chunks = list(iter_chunks(data))
    assert [seq for seq, _ in chunks] == list(range(len(chunks)))
    assert b"".join(part for _, part in chunks) == data


def test_empty_payload_yields_no_chunks():
    assert list(iter_chunks(b"")) == []


def test_reassembler_restores_hash():
    data = b"anydesk-like payload" * 9000
    reassembler = Reassembler()
    for seq, chunk in iter_chunks(data):
        reassembler.add(seq, chunk)
    assert sha256_bytes(reassembler.result()) == hashlib.sha256(data).hexdigest()


def test_reassembler_detects_gap():
    reassembler = Reassembler()
    reassembler.add(0, b"abc")
    with pytest.raises(FileTransferError):
        reassembler.add(2, b"def")


def test_reassembler_rejects_duplicate_chunk():
    reassembler = Reassembler()
    reassembler.add(0, b"abc")
    with pytest.raises(FileTransferError):
        reassembler.add(0, b"abc")


def test_safe_target_name_strips_paths():
    assert safe_target_name("..\\..\\Windows\\evil.exe") == "evil.exe"
    assert safe_target_name("/etc/passwd") == "passwd"
    assert safe_target_name("report.docx") == "report.docx"


def test_safe_target_name_rejects_empty():
    with pytest.raises(FileTransferError):
        safe_target_name("")
