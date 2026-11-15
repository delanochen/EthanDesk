import pytest

from agent.inject import BOUNDS, apply_input, pointer_bounds, unsupported_inputs


def _bounds():
    return {"left": 0, "top": 0, "width": 1920, "height": 1080}


def test_pointer_bounds_defaults_to_primary_monitor():
    assert pointer_bounds({}) == BOUNDS or set(pointer_bounds({})) == {"left", "top", "width", "height"}


def test_move_maps_normalised_coordinates():
    calls = []
    apply_input(
        {"t": "input", "ev": "mouse_move", "x": 0.5, "y": 0.5},
        _bounds(),
        move=lambda x, y: calls.append(("move", x, y)),
    )
    assert calls == [("move", 960, 540)]


def test_button_press_and_release():
    calls = []
    apply_input({"t": "input", "ev": "mouse_down", "button": "left"}, _bounds(), button=lambda b, down: calls.append((b, down)))
    apply_input({"t": "input", "ev": "mouse_up", "button": "right"}, _bounds(), button=lambda b, down: calls.append((b, down)))
    assert calls == [("left", True), ("right", False)]


def test_wheel_delta_forwarded():
    calls = []
    apply_input({"t": "input", "ev": "wheel", "delta": -120}, _bounds(), wheel=lambda d: calls.append(d))
    assert calls == [-120]


def test_key_uses_keysym_translation():
    calls = []
    apply_input({"t": "input", "ev": "key_down", "keysym": "a"}, _bounds(), key=lambda vk, down: calls.append((vk, down)))
    assert calls == [(0x41, True)]


def test_unknown_keysym_is_ignored():
    calls = []
    apply_input({"t": "input", "ev": "key_down", "keysym": "Multi_key"}, _bounds(), key=lambda vk, down: calls.append((vk, down)))
    assert calls == []


def test_unknown_event_is_reported():
    results = unsupported_inputs({"t": "input", "ev": "touch"}, _bounds())
    assert results is True


def test_malformed_message_does_not_raise():
    calls = []
    apply_input({"t": "input", "ev": "mouse_move", "x": "bad", "y": None}, _bounds(), move=lambda x, y: calls.append((x, y)))
    assert calls == []


def test_input_without_ev_is_ignored():
    calls = []
    apply_input({"t": "input"}, _bounds(), move=lambda x, y: calls.append((x, y)))
    assert calls == []


def test_scroll_clamps_extreme_delta():
    calls = []
    apply_input({"t": "input", "ev": "wheel", "delta": 99999}, _bounds(), wheel=lambda d: calls.append(d))
    assert calls and abs(calls[0]) <= 1200


def test_text_event_types_each_utf16_unit():
    """中文/emoji 走 Unicode 注入：keysym 翻译不了，IME 也不一定在远端起效。"""
    typed = []
    apply_input(
        {"t": "input", "ev": "text", "text": "中文"},
        _bounds(),
        type_text=lambda text: typed.append(text),
    )
    assert typed == ["中文"]


def test_text_event_ignores_empty_and_non_string():
    typed = []
    for payload in ("", None, 123):
        apply_input(
            {"t": "input", "ev": "text", "text": payload},
            _bounds(),
            type_text=lambda text: typed.append(text),
        )
    assert typed == []


def test_text_event_is_a_known_event():
    assert unsupported_inputs({"t": "input", "ev": "text"}, _bounds()) is False


def test_text_longer_than_the_cap_is_truncated():
    typed = []
    apply_input(
        {"t": "input", "ev": "text", "text": "a" * 5000},
        _bounds(),
        type_text=lambda text: typed.append(text),
    )
    assert typed and len(typed[0]) <= 1024


def test_type_text_sends_unicode_key_events(monkeypatch):
    """真实注入路径：每个 UTF-16 单元一对 keydown/keyup，wScan 里放该单元。"""
    import agent.inject as inject

    sent = []
    monkeypatch.setattr(inject, "_send", lambda inputs: sent.append(list(inputs)))

    inject.type_text("A中")

    assert len(sent) == 2
    for batch in sent:
        assert len(batch) == 2
        down, up = batch
        # wVk 必须是 0，靠 wScan 传字符；抬起那一条要带 UNICODE|KEYUP。
        assert down.ki.wVk == 0
        assert down.ki.dwFlags == inject.KEYEVENTF_UNICODE
        assert up.ki.dwFlags == inject.KEYEVENTF_UNICODE | inject.KEYEVENTF_KEYUP
    assert sent[0][0].ki.wScan == 0x41
    assert sent[1][0].ki.wScan == 0x4E2D


def test_type_text_handles_surrogate_pairs(monkeypatch):
    """BMP 之外的字符要拆成两个 UTF-16 单元，否则 SendInput 会丢字。"""
    import agent.inject as inject

    batches = []
    monkeypatch.setattr(inject, "_send", lambda inputs: batches.append(list(inputs)))

    inject.type_text("\U0001F600")  # 😀

    assert len(batches) == 2
    units = [batch[0].ki.wScan for batch in batches]
    assert units == [0xD83D, 0xDE00]


def test_type_text_noop_for_empty_and_off_windows(monkeypatch):
    import sys

    import agent.inject as inject

    calls = []
    monkeypatch.setattr(inject, "_send", lambda inputs: calls.append(inputs))
    inject.type_text("")
    inject.type_text(None)
    monkeypatch.setattr(sys, "platform", "linux")
    inject.type_text("a")
    assert calls == []


def test_key_input_instances_share_one_ctypes_class():
    """批次里必须都是同一个 ctypes 结构类，否则组数组会 TypeError。

    真机症状：中文注入时报 ``incompatible types, INPUT instance instead of INPUT
    instance``，整个远控会话被踢断。
    """
    import agent.inject as inject

    first = inject._key_input(0, inject.KEYEVENTF_UNICODE, 0x41)
    second = inject._key_input(0, inject.KEYEVENTF_UNICODE, 0x4E2D)
    mouse = inject._mouse_input(inject.MOUSEEVENTF_LEFTDOWN)

    assert type(first) is type(second) is type(mouse)


def test_send_batches_two_unicode_events(monkeypatch):
    """一次 SendInput 送 down+up，就是真机崩过的那条路径。"""
    import ctypes as real_ctypes

    import agent.inject as inject

    captured = {}

    class FakeUser32:
        def SendInput(self, count, array, size):
            captured["count"] = count
            captured["size"] = size
            return count

    fake_ctypes = type("FakeCtypes", (), {})()
    fake_ctypes.sizeof = real_ctypes.sizeof
    fake_ctypes.windll = type("W", (), {"user32": FakeUser32()})()
    monkeypatch.setattr(inject, "ctypes", fake_ctypes)
    monkeypatch.setattr(inject.sys, "platform", "win32")

    inject.type_text("中")

    assert captured["count"] == 2
    assert captured["size"] > 0


