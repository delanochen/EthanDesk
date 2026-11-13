"""Minimal HMAC-signed tokens for viewer sessions.

The relay is the only issuer. A token is ``base64url(payload).base64url(mac)`` where the
payload is JSON ``{"sub": <subject>, "exp": <unix seconds>}`` and the MAC is
HMAC-SHA256 over the encoded payload. Verification is constant-time and rejects
truncation, tampering and expiry.
"""

from __future__ import annotations

import base64
import binascii
import hashlib
import hmac
import json
import time
from typing import Any

DEFAULT_TTL = 12 * 3600


class AuthError(Exception):
    """Raised when a token is missing, malformed, tampered with or expired."""


def _b64encode(raw: bytes) -> str:
    return base64.urlsafe_b64encode(raw).decode("ascii").rstrip("=")


def _b64decode(text: str) -> bytes:
    padding = "=" * (-len(text) % 4)
    return base64.urlsafe_b64decode(text + padding)


def _sign(secret: str, body: str) -> str:
    mac = hmac.new(secret.encode("utf-8"), body.encode("utf-8"), hashlib.sha256).digest()
    return _b64encode(mac)


def issue_token(secret: str, subject: str, ttl: int = DEFAULT_TTL, now: float | None = None) -> str:
    """Create a signed token for ``subject``."""
    issued = time.time() if now is None else now
    payload = {"sub": subject, "exp": int(issued) + int(ttl)}
    body = _b64encode(json.dumps(payload, separators=(",", ":")).encode("utf-8"))
    return f"{body}.{_sign(secret, body)}"


def verify_token(secret: str, token: str, now: float | None = None) -> dict[str, Any]:
    """Return the decoded payload, or raise :class:`AuthError`."""
    if not isinstance(token, str) or token.count(".") != 1:
        raise AuthError("malformed token")
    body, signature = token.split(".", 1)
    if not body or not signature:
        raise AuthError("malformed token")

    expected = _sign(secret, body)
    if not hmac.compare_digest(expected, signature):
        raise AuthError("bad signature")

    try:
        payload = json.loads(_b64decode(body))
    except (binascii.Error, ValueError, TypeError) as exc:
        raise AuthError("unreadable payload") from exc
    if not isinstance(payload, dict):
        raise AuthError("unreadable payload")

    expiry = payload.get("exp")
    if not isinstance(expiry, (int, float)):
        raise AuthError("missing expiry")
    current = time.time() if now is None else now
    if current > float(expiry):
        raise AuthError("token expired")
    return payload
