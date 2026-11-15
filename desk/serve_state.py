"""首页「允许他人协助本机」的状态文案。

只由两件事决定：开关是否打开、中继是否确认本机在线。手动启停不参与，
这样用户只需要看一句话就知道本机现在能不能被协助。
"""

SERVE_CONNECTING = "connecting"
SERVE_ONLINE = "online"
SERVE_OFFLINE = "offline"

_CLOSED = "已关闭"
_RECEIVABLE = "可接收协助"
_DIALLING = "正在连接中继…"
_RETRYING = "中继未连接，自动重试中…"


def serve_status_text(enabled: bool, relay_state: str) -> str:
    if not enabled:
        return _CLOSED
    if relay_state == SERVE_ONLINE:
        return _RECEIVABLE
    if relay_state == SERVE_OFFLINE:
        return _RETRYING
    return _DIALLING
