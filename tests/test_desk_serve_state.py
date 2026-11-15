"""首页「允许他人协助本机」只有一个开关，它的文案由中继状态决定。"""

from desk.serve_state import SERVE_CONNECTING, SERVE_OFFLINE, SERVE_ONLINE, serve_status_text


def test_switch_off_reads_closed():
    assert serve_status_text(False, SERVE_ONLINE) == "已关闭"


def test_switch_on_after_relay_confirm_reads_receivable():
    assert serve_status_text(True, SERVE_ONLINE) == "可接收协助"


def test_switch_on_while_dialling_reads_connecting():
    assert serve_status_text(True, SERVE_CONNECTING) == "正在连接中继…"


def test_switch_on_with_lost_relay_does_not_read_receivable():
    text = serve_status_text(True, SERVE_OFFLINE)
    assert text != "可接收协助"
    assert "重试" in text


def test_unknown_relay_state_never_reads_receivable():
    assert serve_status_text(True, "something-else") != "可接收协助"
