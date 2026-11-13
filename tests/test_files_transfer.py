import hashlib
import os

import pytest

from agent.files_transfer import FileTransferError, IncomingFile, OutgoingFile


def test_outgoing_offer_carries_size_and_chunks():
    payload = b"report" * 400_000
    sender = OutgoingFile("t1", "report.docx", payload)
    offer = sender.offer()
    assert offer["t"] == "file_offer"
    assert offer["name"] == "report.docx"
    assert offer["size"] == len(payload)
    assert offer["sha256"] == hashlib.sha256(payload).hexdigest()
    assert offer["direction"] == "down"


def test_outgoing_frames_pair_header_with_payload():
    payload = bytes(range(256)) * 8192  # 2MiB -> two 1MiB chunks
    sender = OutgoingFile("t1", "blob.bin", payload)
    pairs = list(sender.frames())
    assert len(pairs) == 2
    for seq, (header, data) in enumerate(pairs):
        assert header["t"] == "bin"
        assert header["kind"] == "file"
        assert header["id"] == "t1"
        assert header["seq"] == seq
        assert header["size"] == len(data)


def test_incoming_writes_file_and_verifies_hash(tmp_path):
    payload = b"payload" * 200_000
    receiver = IncomingFile("t1", "out.bin", len(payload), str(tmp_path))
    for seq, (header, data) in enumerate(OutgoingFile("t1", "out.bin", payload).frames()):
        receiver.feed(header["seq"] if "seq" in header else seq, data)
    path = receiver.finish()
    written = open(path, "rb").read()
    assert hashlib.sha256(written).hexdigest() == hashlib.sha256(payload).hexdigest()


def test_incoming_sanitises_path_traversal(tmp_path):
    receiver = IncomingFile("t2", "..\\..\\Windows\\evil.dll", 3, str(tmp_path))
    receiver.feed(0, b"abc")
    path = receiver.finish()
    assert os.path.dirname(path) == str(tmp_path)
    assert os.path.basename(path) == "evil.dll"


def test_incoming_rejects_size_mismatch(tmp_path):
    receiver = IncomingFile("t3", "short.bin", 10, str(tmp_path))
    receiver.feed(0, b"abc")
    with pytest.raises(FileTransferError):
        receiver.finish()


def test_incoming_rejects_out_of_order_chunk(tmp_path):
    receiver = IncomingFile("t4", "gap.bin", 6, str(tmp_path))
    receiver.feed(0, b"abc")
    with pytest.raises(FileTransferError):
        receiver.feed(2, b"def")
