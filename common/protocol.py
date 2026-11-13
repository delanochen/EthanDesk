"""Wire protocol: JSON envelopes plus the rule that pairs binary payloads with a header.

Every text message carries a ``t`` field. Known types are listed in :data:`KNOWN_TYPES`;
anything else decodes to ``None`` so a peer can ignore forward-compatible extensions
instead of crashing.

Binary messages (JPEG frames, file chunks) are never self-describing on the wire, so the
sender must emit a ``{"t": "bin", "kind": ..., ...}`` header immediately before the
binary message. :class:`BinaryPairer` enforces that ordering on the receiving side.
"""

from __future__ import annotations

import json
from typing import Any

KNOWN_TYPES = frozenset(
    {
        "hello",
        "hello_ok",
        "error",
        "ping",
        "pong",
        "session_request",
        "session_accept",
        "session_denied",
        "session_start",
        "session_close",
        "meta",
        "input",
        "set_quality",
        "frame_recv",
        "pause",
        "clip",
        "clip_ack",
        "file_offer",
        "file_accept",
        "file_reject",
        "file_done",
        "bin",
        "log",
    }
)


class ProtocolError(Exception):
    """Raised when a message cannot be understood or breaks ordering rules."""


def encode(message: dict[str, Any]) -> str:
    """Serialise one text envelope. Compact separators keep frame headers small."""
    return json.dumps(message, ensure_ascii=False, separators=(",", ":"))


def decode(data: str | bytes) -> dict[str, Any] | None:
    """Parse one text envelope.

    Returns the message dict for a known ``t``, ``None`` for unknown or non-object
    payloads, and raises :class:`ProtocolError` when the bytes are not valid JSON.
    """
    try:
        parsed = json.loads(data)
    except (ValueError, TypeError) as exc:
        raise ProtocolError(f"invalid json payload: {exc}") from exc
    if not isinstance(parsed, dict):
        return None
    msg_type = parsed.get("t")
    if not isinstance(msg_type, str) or msg_type not in KNOWN_TYPES:
        return None
    return parsed


def make_bin_header(kind: str, **fields: Any) -> dict[str, Any]:
    """Build the header that must precede a binary message."""
    header: dict[str, Any] = {"t": "bin", "kind": kind}
    header.update(fields)
    return header


def is_bin_header(message: Any) -> bool:
    return isinstance(message, dict) and message.get("t") == "bin"


class BinaryPairer:
    """Track the header/payload pairing state of a single peer connection."""

    def __init__(self) -> None:
        self._header: dict[str, Any] | None = None

    @property
    def pending(self) -> bool:
        return self._header is not None

    def header(self, message: dict[str, Any]) -> None:
        if not is_bin_header(message):
            raise ProtocolError("binary payload requires a leading bin header")
        if self._header is not None:
            raise ProtocolError("previous bin header received no payload")
        self._header = message

    def attach(self, data: bytes) -> tuple[dict[str, Any], bytes]:
        if self._header is None:
            raise ProtocolError("binary payload arrived without a bin header")
        header, self._header = self._header, None
        return header, data
