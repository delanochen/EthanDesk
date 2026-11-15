"""Contract tests for the unified AweSun-style client."""

from __future__ import annotations


def test_module_imports_without_display():
    import desk.client as client

    assert client.PAGES == ("远程协助", "设备", "会话", "设置")
    assert client.SETTINGS_PAGES == ("本机信息", "常规", "安全", "网络", "画面")


def test_mask_code_hides_digits():
    from desk.client import mask_code

    assert mask_code("246810") == "••• •••"
    assert mask_code("") == ""
    assert mask_code("12345") == "••• •••"


def test_connect_button_label_matches_state():
    from desk.client import connect_label

    assert connect_label(True) == "断开"
    assert connect_label(False) == "连接"


def test_window_builds_every_page():
    import tkinter as tk

    import pytest

    from desk.client import PAGES, DeskWindow

    try:
        window = DeskWindow(start_threads=False)
    except tk.TclError as exc:
        pytest.skip(f"no display: {exc}")
    try:
        assert set(window.pages) == set(PAGES)
        for frame in window.pages.values():
            assert frame.winfo_exists()
    finally:
        window.root.destroy()
