"""Composite screen capture for Windows sessions without a display surface.

``mss`` needs an attached desktop: in an RDP session that has been disconnected
``BitBlt`` fails with ``WinError 258`` and there is simply no framebuffer to read.
Individual windows, however, still paint themselves through ``PrintWindow``, so
this module enumerates the visible top-level windows in z-order, renders each into
a bitmap and pastes them onto a canvas at their screen coordinates.

It is a fallback, not a replacement: wallpaper and the taskbar-free desktop
backdrop are missing, but everything an application draws is visible and a
remote session stays usable on a headless VM.
"""

from __future__ import annotations

import ctypes
from ctypes import wintypes
from typing import Iterator

from PIL import Image

PGN_RENDERFULLCONTENT = 0x00000002
BI_RGB = 0
DIB_RGB_COLORS = 0
SRCCOPY = 0x00CC0020
DWMWA_CLOAKED = 14
MIN_WINDOW_SIDE = 40
BACKGROUND = (24, 24, 28)

SM_XVIRTUALSCREEN = 76
SM_YVIRTUALSCREEN = 77
SM_CXVIRTUALSCREEN = 78
SM_CYVIRTUALSCREEN = 79

user32 = ctypes.windll.user32
gdi32 = ctypes.windll.gdi32
dwmapi = ctypes.windll.dwmapi
try:
    user32.SetProcessDPIAware()
except Exception:  # pragma: no cover - older Windows
    pass


class BITMAPINFOHEADER(ctypes.Structure):
    _fields_ = [
        ("biSize", wintypes.DWORD),
        ("biWidth", wintypes.LONG),
        ("biHeight", wintypes.LONG),
        ("biPlanes", wintypes.WORD),
        ("biBitCount", wintypes.WORD),
        ("biCompression", wintypes.DWORD),
        ("biSizeImage", wintypes.DWORD),
        ("biXPelsPerMeter", wintypes.LONG),
        ("biYPelsPerMeter", wintypes.LONG),
        ("biClrUsed", wintypes.DWORD),
        ("biClrImportant", wintypes.DWORD),
    ]


class BITMAPINFO(ctypes.Structure):
    _fields_ = [("bmiHeader", BITMAPINFOHEADER), ("bmiColors", wintypes.DWORD * 3)]


def virtual_screen() -> tuple[int, int, int, int]:
    """Return ``(left, top, width, height)`` of the virtual screen."""
    return (
        user32.GetSystemMetrics(SM_XVIRTUALSCREEN),
        user32.GetSystemMetrics(SM_YVIRTUALSCREEN),
        user32.GetSystemMetrics(SM_CXVIRTUALSCREEN),
        user32.GetSystemMetrics(SM_CYVIRTUALSCREEN),
    )


def _is_cloaked(hwnd: int) -> bool:
    value = ctypes.c_int(0)
    try:
        result = dwmapi.DwmGetWindowAttribute(
            wintypes.HWND(hwnd), ctypes.c_uint(DWMWA_CLOAKED), ctypes.byref(value), ctypes.sizeof(value)
        )
    except Exception:
        return False
    return result == 0 and value.value != 0


def _top_level_windows() -> Iterator[int]:
    hwnd = user32.GetTopWindow(None)
    while hwnd:
        yield hwnd
        hwnd = user32.GetWindow(hwnd, 2)  # GW_HWNDNEXT


def iter_visible_windows() -> Iterator[tuple[int, tuple[int, int, int, int]]]:
    """Yield ``(hwnd, (left, top, width, height))`` for paintable top-level windows."""
    for hwnd in _top_level_windows():
        if not user32.IsWindowVisible(hwnd):
            continue
        if _is_cloaked(hwnd):
            continue
        rect = wintypes.RECT()
        if not user32.GetWindowRect(hwnd, ctypes.byref(rect)):
            continue
        width = rect.right - rect.left
        height = rect.bottom - rect.top
        if width < MIN_WINDOW_SIDE or height < MIN_WINDOW_SIDE:
            continue
        if width > 10000 or height > 10000:
            continue
        yield hwnd, (rect.left, rect.top, width, height)


def capture_window(hwnd: int, width: int, height: int) -> Image.Image | None:
    """Render one window with ``PrintWindow``; ``None`` when the window refuses."""
    window_dc = user32.GetWindowDC(hwnd)
    if not window_dc:
        return None
    memory_dc = gdi32.CreateCompatibleDC(window_dc)
    bitmap = gdi32.CreateCompatibleBitmap(window_dc, width, height)
    previous = gdi32.SelectObject(memory_dc, bitmap)
    try:
        if not user32.PrintWindow(hwnd, memory_dc, PGN_RENDERFULLCONTENT):
            return None
        info = BITMAPINFO()
        info.bmiHeader.biSize = ctypes.sizeof(BITMAPINFOHEADER)
        info.bmiHeader.biWidth = width
        info.bmiHeader.biHeight = -height
        info.bmiHeader.biPlanes = 1
        info.bmiHeader.biBitCount = 32
        info.bmiHeader.biCompression = BI_RGB
        buffer = ctypes.create_string_buffer(width * height * 4)
        rows = gdi32.GetDIBits(
            memory_dc, bitmap, 0, height, buffer, ctypes.byref(info), DIB_RGB_COLORS
        )
        if rows == 0:
            return None
        return Image.frombuffer("RGB", (width, height), buffer, "raw", "BGRX", 0, 1)
    finally:
        if previous:
            gdi32.SelectObject(memory_dc, previous)
        gdi32.DeleteObject(bitmap)
        gdi32.DeleteDC(memory_dc)
        user32.ReleaseDC(hwnd, window_dc)


class WindowCompositeCapturer:
    """Builds a desktop picture out of the windows that do paint."""

    def __init__(self) -> None:
        self._last_warning = ""

    @property
    def bounds(self) -> dict[str, int]:
        left, top, width, height = virtual_screen()
        if width <= 0 or height <= 0:
            width, height = 1920, 1080
        return {"left": left, "top": top, "width": width, "height": height}

    def grab(self) -> Image.Image:
        left, top, width, height = virtual_screen()
        if width <= 0 or height <= 0:
            raise OSError("session has no virtual screen")
        canvas = Image.new("RGB", (width, height), BACKGROUND)
        painted = 0
        for hwnd, (x, y, w, h) in iter_visible_windows():
            image = capture_window(hwnd, w, h)
            if image is None:
                continue
            canvas.paste(image, (x - left, y - top))
            painted += 1
        if painted == 0:
            raise OSError("no paintable windows in this session")
        return canvas

    def close(self) -> None:
        return None
