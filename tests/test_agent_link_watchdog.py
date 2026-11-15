"""链路停滞看门狗：半开的 WebSocket 必须能被判定为死亡。"""

from __future__ import annotations

from agent.app import STALL_TIMEOUT, link_stalled


def test_link_stalled_false_when_nothing_received_yet():
    assert link_stalled(0.0, 10_000.0) is False


def test_link_stalled_false_inside_the_window():
    assert link_stalled(100.0, 100.0 + STALL_TIMEOUT - 1.0) is False


def test_link_stalled_true_past_the_window():
    assert link_stalled(100.0, 100.0 + STALL_TIMEOUT + 1.0) is True


def test_stall_timeout_is_longer_than_the_known_edge_stalls():
    """链路实测有 28-30 秒级停顿；阈值必须留出余量，否则会误杀正常连接。"""
    assert STALL_TIMEOUT >= 45.0
