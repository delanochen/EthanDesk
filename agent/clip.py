"""Unicode clipboard access through ctypes (no pywin32 dependency)."""

from __future__ import annotations

import ctypes
import sys
import time
from ctypes import wintypes

CF_UNICODETEXT = 13
GMEM_MOVEABLE = 0x0002
OPEN_RETRIES = 12
OPEN_DELAY = 0.01


class ClipboardUnavailable(Exception):
    """Raised when the clipboard cannot be opened/read, or input is invalid."""


def _user32():
    if sys.platform != "win32":
        raise ClipboardUnavailable("clipboard is only available on Windows")
    return ctypes.windll.user32


def _kernel32():
    if sys.platform != "win32":
        raise ClipboardUnavailable("clipboard is only available on Windows")
    return ctypes.windll.kernel32


def _open_clipboard(user32) -> None:
    for _ in range(OPEN_RETRIES):
        if user32.OpenClipboard(None):
            return
        time.sleep(OPEN_DELAY)
    raise ClipboardUnavailable("clipboard is locked by another process")


def get_text() -> str:
    """Return the clipboard's unicode text, or ``""`` when it holds none."""
    user32 = _user32()
    kernel32 = _kernel32()
    user32.GetClipboardData.restype = wintypes.HANDLE
    user32.GetClipboardData.argtypes = [wintypes.UINT]
    kernel32.GlobalLock.restype = ctypes.c_void_p
    kernel32.GlobalLock.argtypes = [wintypes.HANDLE]
    kernel32.GlobalUnlock.argtypes = [wintypes.HANDLE]

    _open_clipboard(user32)
    try:
        handle = user32.GetClipboardData(CF_UNICODETEXT)
        if not handle:
            return ""
        pointer = kernel32.GlobalLock(handle)
        if not pointer:
            return ""
        try:
            return ctypes.wstring_at(pointer)
        finally:
            kernel32.GlobalUnlock(handle)
    finally:
        user32.CloseClipboard()


def set_text(text: str) -> None:
    """Replace the clipboard contents with ``text``."""
    if not isinstance(text, str):
        raise ClipboardUnavailable("clipboard text must be a string")

    user32 = _user32()
    kernel32 = _kernel32()
    user32.EmptyClipboard.restype = wintypes.BOOL
    user32.SetClipboardData.restype = wintypes.HANDLE
    user32.SetClipboardData.argtypes = [wintypes.UINT, wintypes.HANDLE]
    kernel32.GlobalAlloc.restype = wintypes.HANDLE
    kernel32.GlobalAlloc.argtypes = [wintypes.UINT, ctypes.c_size_t]
    kernel32.GlobalLock.restype = ctypes.c_void_p
    kernel32.GlobalLock.argtypes = [wintypes.HANDLE]
    kernel32.GlobalUnlock.argtypes = [wintypes.HANDLE]
    kernel32.GlobalFree.restype = wintypes.HANDLE
    kernel32.GlobalFree.argtypes = [wintypes.HANDLE]

    _open_clipboard(user32)
    try:
        user32.EmptyClipboard()
        size = (len(text) + 1) * ctypes.sizeof(ctypes.c_wchar)
        handle = kernel32.GlobalAlloc(GMEM_MOVEABLE, size)
        if not handle:
            raise ClipboardUnavailable("cannot allocate clipboard memory")
        pointer = kernel32.GlobalLock(handle)
        if pointer:
            ctypes.memmove(pointer, ctypes.create_unicode_buffer(text), size)
            kernel32.GlobalUnlock(handle)
        # Ownership of the handle transfers to the clipboard on success.
        if not user32.SetClipboardData(CF_UNICODETEXT, handle):
            kernel32.GlobalFree(handle)
            raise ClipboardUnavailable("cannot set clipboard data")
    finally:
        user32.CloseClipboard()


def text_signature(text: str) -> str:
    """Cheap change detector for clipboard polling."""
    return f"{len(text)}:{text[:64]}"
