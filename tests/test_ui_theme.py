"""Contract tests for the shared AweSun-style UI shell."""

from __future__ import annotations

import re


def test_palette_is_dark_and_complete():
    from ui.theme import PALETTE

    for key in (
        "bg",
        "sidebar",
        "panel",
        "border",
        "text",
        "muted",
        "accent",
        "accent_hover",
        "ok",
        "danger",
    ):
        assert key in PALETTE, key
        assert re.fullmatch(r"#[0-9A-Fa-f]{6}", PALETTE[key]), (key, PALETTE[key])


def test_page_names_match_the_shell():
    from ui.theme import AGENT_PAGES, VIEWER_PAGES

    assert VIEWER_PAGES == ("远程协助", "设备", "会话", "设置")
    assert AGENT_PAGES == ("远程协助", "设置")


def test_format_ident_groups_identification_code():
    from ui.theme import format_ident

    assert format_ident("0965702962") == "096 570 2962"
    assert format_ident("448098688") == "448 098 688"
    assert format_ident("096 570 2962") == "096 570 2962"
    assert format_ident("12345") == "12 345"
    assert format_ident("") == ""


def test_apply_theme_configures_ttk():
    import tkinter as tk
    import tkinter.ttk as ttk

    import pytest

    from ui.theme import PALETTE, apply_theme

    try:
        root = tk.Tk()
    except tk.TclError as exc:  # no display available
        pytest.skip(f"no display: {exc}")
    try:
        style = apply_theme(root)
        assert isinstance(style, ttk.Style)
        assert style.theme_use() == "clam"
        assert style.lookup("TFrame", "background").lower() == PALETTE["bg"].lower()
        assert style.lookup("Sidebar.TFrame", "background").lower() == PALETTE["sidebar"].lower()
        assert style.lookup("Accent.TButton", "background").lower() == PALETTE["accent"].lower()
    finally:
        root.destroy()


def test_apply_theme_survives_headless_absence():
    """apply_theme must not import Tk at module import time."""
    import ui.theme as theme

    assert theme.PALETTE["bg"].startswith("#")
    assert callable(theme.apply_theme)
