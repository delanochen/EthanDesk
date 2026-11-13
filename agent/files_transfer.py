"""File transfer pieces shared by the agent and the viewer.

The wire format is one ``file_offer`` JSON message followed by ``(bin header, chunk)``
pairs, all over the same session websocket that carries picture frames. Chunks carry
their own ``seq`` so the receiver can reject gaps and duplicates.
"""

from __future__ import annotations

import hashlib
import os
from typing import Iterator

from common.files import CHUNK_SIZE, FileTransferError, iter_chunks, safe_target_name
from common.protocol import make_bin_header


class OutgoingFile:
    """A file being pushed to the peer."""

    def __init__(
        self,
        transfer_id: str,
        name: str,
        data: bytes,
        direction: str = "down",
        chunk_size: int = CHUNK_SIZE,
    ) -> None:
        self.transfer_id = transfer_id
        self.name = name
        self.data = data
        self.direction = direction
        self.chunk_size = chunk_size

    @property
    def size(self) -> int:
        return len(self.data)

    def offer(self) -> dict:
        return {
            "t": "file_offer",
            "id": self.transfer_id,
            "name": safe_target_name(self.name),
            "size": self.size,
            "sha256": hashlib.sha256(self.data).hexdigest(),
            "direction": self.direction,
        }

    def frames(self) -> Iterator[tuple[dict, bytes]]:
        for seq, chunk in iter_chunks(self.data, self.chunk_size):
            header = make_bin_header(
                "file",
                id=self.transfer_id,
                seq=seq,
                size=len(chunk),
                total=self.size,
            )
            yield header, chunk


class IncomingFile:
    """A file being received into ``target_dir``."""

    def __init__(
        self,
        transfer_id: str,
        name: str,
        expected_size: int,
        target_dir: str,
        expected_sha256: str | None = None,
    ) -> None:
        self.transfer_id = transfer_id
        self.name = safe_target_name(name)
        self.expected_size = int(expected_size)
        self.target_dir = target_dir
        self.expected_sha256 = expected_sha256
        self._parts: list[bytes] = []
        self._next_seq = 0

    @property
    def received(self) -> int:
        return sum(len(part) for part in self._parts)

    def feed(self, seq: int, data: bytes) -> None:
        if seq != self._next_seq:
            raise FileTransferError(f"unexpected chunk {seq}, expected {self._next_seq}")
        if self.received + len(data) > self.expected_size:
            raise FileTransferError("received more bytes than the offer declared")
        self._parts.append(data)
        self._next_seq += 1

    def finish(self) -> str:
        payload = b"".join(self._parts)
        if len(payload) != self.expected_size:
            raise FileTransferError(
                f"size mismatch: expected {self.expected_size}, got {len(payload)}"
            )
        if self.expected_sha256:
            digest = hashlib.sha256(payload).hexdigest()
            if digest != self.expected_sha256:
                raise FileTransferError("sha256 mismatch")

        os.makedirs(self.target_dir, exist_ok=True)
        path = os.path.join(self.target_dir, self.name)
        with open(path, "wb") as handle:
            handle.write(payload)
        return path
