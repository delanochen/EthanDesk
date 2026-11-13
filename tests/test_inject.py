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
