"""File transfer helpers: deterministic chunking, ordered reassembly and safe names."""

from __future__ import annotations

import hashlib
import ntpath
import posixpath

CHUNK_SIZE = 1024 * 1024


class FileTransferError(Exception):
    """Raised when a transfer violates ordering or naming expectations."""


def sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def iter_chunks(data: bytes, chunk_size: int = CHUNK_SIZE) -> list[tuple[int, bytes]]:
    """Split ``data`` into numbered chunks; empty payloads produce no chunks."""
    if chunk_size <= 0:
        raise ValueError("chunk_size must be positive")
    return [
        (index, data[offset : offset + chunk_size])
        for index, offset in enumerate(range(0, len(data), chunk_size))
    ]


def iter_file_chunks(path: str, chunk_size: int = CHUNK_SIZE) -> list[tuple[int, bytes]]:
    with open(path, "rb") as handle:
        return iter_chunks(handle.read(), chunk_size)


class Reassembler:
    """Collect chunks in order and reject gaps or duplicates."""

    def __init__(self) -> None:
        self._parts: list[bytes] = []
        self._next = 0

    @property
    def next_seq(self) -> int:
        return self._next

    def add(self, seq: int, data: bytes) -> None:
        if seq != self._next:
            raise FileTransferError(f"unexpected chunk {seq}, expected {self._next}")
        self._parts.append(data)
        self._next += 1

    def result(self) -> bytes:
        return b"".join(self._parts)


def safe_target_name(name: str) -> str:
    """Reduce a peer-supplied path to a bare filename to prevent directory escape."""
    if not isinstance(name, str):
        raise FileTransferError("file name must be a string")
    candidate = name.replace("\\", "/")
    base = posixpath.basename(candidate) or ntpath.basename(name)
    base = base.strip().strip(".")
    if not base:
        raise FileTransferError("empty file name")
    return base
