"""主控端按键分流：可打印字符按字符注入，Ctrl/Alt 组合键仍走虚拟键。

这两个处理函数挂在 Tk 画布上，真机上"输入法开着就打不进字"就是从这里来的，
所以不能只测公共函数，还得确认两个窗口类真的把事件发对了。
"""

from __future__ import annotations

import queue


class _Event:
    def __init__(self, keysym: str, char: str = "", state: int = 0) -> None:
        self.keysym = keysym
        self.char = char
        self.state = state


class _Sink:
    """只要一个队列，够 _on_key 用，不用真的建 Tk 窗口。"""

    def __init__(self) -> None:
        self.commands = queue.Queue()
        self.viewer_commands = self.commands


SHIFT = 0x0001
CONTROL = 0x0004


def _drain(q: queue.Queue) -> list:
    items = []
    while True:
        try:
            items.append(q.get_nowait())
        except queue.Empty:
            return items


def test_viewer_sends_text_for_printable_characters():
    from viewer.app import ViewerWindow

    sink = _Sink()
    ViewerWindow._on_key(sink, _Event("a", "a", 0), True)
    events = _drain(sink.commands)
    assert events == [("input", {"t": "input", "ev": "text", "text": "a"})]


def test_viewer_sends_uppercase_as_text_even_with_shift():
    from viewer.app import ViewerWindow

    sink = _Sink()
    ViewerWindow._on_key(sink, _Event("A", "A", SHIFT), True)
    events = _drain(sink.commands)
    assert events == [("input", {"t": "input", "ev": "text", "text": "A"})]


def test_viewer_keeps_control_combinations_as_virtual_keys():
    from viewer.app import ViewerWindow

    sink = _Sink()
    ViewerWindow._on_key(sink, _Event("c", "\x03", CONTROL), True)
    ViewerWindow._on_key(sink, _Event("c", "\x03", CONTROL), False)
    events = _drain(sink.commands)
    assert events == [
        ("input", {"t": "input", "ev": "key_down", "keysym": "c"}),
        ("input", {"t": "input", "ev": "key_up", "keysym": "c"}),
    ]


def test_viewer_keeps_non_printable_keys_as_virtual_keys():
    from viewer.app import ViewerWindow

    sink = _Sink()
    ViewerWindow._on_key(sink, _Event("Return", "\r", 0), True)
    ViewerWindow._on_key(sink, _Event("Return", "\r", 0), False)
    events = _drain(sink.commands)
    assert events == [
        ("input", {"t": "input", "ev": "key_down", "keysym": "Return"}),
        ("input", {"t": "input", "ev": "key_up", "keysym": "Return"}),
    ]


def test_desk_client_sends_text_for_printable_characters():
    from desk.client import DeskWindow

    sink = _Sink()
    DeskWindow._on_key(sink, _Event("中", "中", 0), True)
    events = _drain(sink.commands)
    assert events == [("input", {"t": "input", "ev": "text", "text": "中"})]


def test_desk_client_keeps_paste_combination_as_virtual_key():
    from desk.client import DeskWindow

    sink = _Sink()
    DeskWindow._on_key(sink, _Event("v", "\x16", CONTROL), True)
    DeskWindow._on_key(sink, _Event("v", "\x16", CONTROL), False)
    events = _drain(sink.commands)
    assert events == [
        ("input", {"t": "input", "ev": "key_down", "keysym": "v"}),
        ("input", {"t": "input", "ev": "key_up", "keysym": "v"}),
    ]
