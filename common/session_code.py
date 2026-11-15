"""Session codes: the short numeric secret a viewer must supply to connect.

AweSun-style behaviour - the code shown on the controlled machine stays fixed,
rotates once a day, or rotates after every session.  It is a convenience check
layered on top of the relay token: the relay still requires a viewer login
before it forwards a single frame.
"""

from __future__ import annotations

import hmac
import secrets
import time

CODE_DIGITS = 6

POLICY_EVERY_SESSION = "every_session"
POLICY_DAILY = "daily"
POLICY_NEVER = "never"

POLICIES = (POLICY_EVERY_SESSION, POLICY_DAILY, POLICY_NEVER)


def generate_code(digits: int = CODE_DIGITS) -> str:
    """Return a fresh numeric code such as ``"482913"``."""
    return "".join(secrets.choice("0123456789") for _ in range(digits))


def normalise(value: object) -> str:
    """Strip the spaces and dashes people paste into the code box.

    Anything that is not exactly ``CODE_DIGITS`` digits becomes ``""``.
    """
    text = "".join(ch for ch in str(value or "") if ch not in " -_")
    if len(text) != CODE_DIGITS or not text.isdigit():
        return ""
    return text


def matches(expected: str, supplied: object) -> bool:
    """Constant-time comparison of the configured code with what the viewer sent."""
    wanted = normalise(expected)
    got = normalise(supplied)
    if not wanted or not got:
        return False
    return hmac.compare_digest(wanted, got)


def should_rotate(policy: str, rotated_at: float, now: float | None = None) -> bool:
    """Whether the stored code has aged past the configured policy."""
    current_time = time.time() if now is None else now
    if policy == POLICY_EVERY_SESSION:
        return True
    if policy == POLICY_DAILY:
        return time.localtime(current_time)[:3] != time.localtime(rotated_at)[:3]
    return False
