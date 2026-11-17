"""把 Windows 账户凭据放进 WebSocket 握手的请求头。

请求头只能承载可见 ASCII，所以值统一用 base64url 编码；放在头里而不是 URL 查询串，
是因为查询串会被 nginx / cloudflared 的访问日志原样记下来，而密码不该躺在那里面。
"""

from __future__ import annotations

import base64

HEADER_USER = "X-EthanDesk-User"
HEADER_PASSWORD = "X-EthanDesk-Password"


def encode(value: str) -> str:
    return base64.urlsafe_b64encode(str(value or "").encode("utf-8")).decode("ascii")


def decode(value: str) -> str:
    text = str(value or "").strip()
    if not text:
        return ""
    padding = "=" * (-len(text) % 4)
    try:
        return base64.urlsafe_b64decode(text + padding).decode("utf-8", "replace")
    except (base64.binascii.Error, ValueError):
        return ""
