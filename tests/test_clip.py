import time

import pytest

from agent.clip import ClipboardUnavailable, get_text, set_text


def _wait_for_clipboard():
    # Windows clipboard is globally locked briefly after a write; give it a moment.
    time.sleep(0.05)


def test_set_then_get_roundtrip():
    payload = "remote-desk clipboard test äöü ✅"
    set_text(payload)
    _wait_for_clipboard()
    assert get_text() == payload


def test_empty_text_is_readable():
    set_text("")
    _wait_for_clipboard()
    assert get_text() == ""


def test_non_string_input_rejected():
    with pytest.raises(ClipboardUnavailable):
        set_text(None)  # type: ignore[arg-type]
