"""Mouse and keyboard injection through Win32 ``SendInput``/``SetCursorPos``.

``apply_input`` is the testable dispatcher: it validates the incoming message, converts
normalised coordinates with :mod:`common.coords`, and calls the injected backend
functions. The default backends are the real Win32 calls below.
"""

from __future__ import annotations

import ctypes
import sys
from typing import Any, Callable

from common.coords import norm_to_pixel
from common.keys import keysym_to_vk

BOUNDS: dict[str, int] = {"left": 0, "top": 0, "width": 1920, "height": 1080}

INPUT_EVENTS = frozenset(
    {"mouse_move", "mouse_down", "mouse_up", "wheel", "key_down", "key_up"}
)

MAX_WHEEL_DELTA = 1200

# Win32 constants
MOUSEEVENTF_LEFTDOWN = 0x0002
MOUSEEVENTF_LEFTUP = 0x0004
MOUSEEVENTF_RIGHTDOWN = 0x0008
MOUSEEVENTF_RIGHTUP = 0x0010
MOUSEEVENTF_MIDDLEDOWN = 0x0020
MOUSEEVENTF_MIDDLEUP = 0x0040
MOUSEEVENTF_WHEEL = 0x0800
KEYEVENTF_KEYUP = 0x0002

_BUTTON_FLAGS = {
    "left": (MOUSEEVENTF_LEFTDOWN, MOUSEEVENTF_LEFTUP),
    "right": (MOUSEEVENTF_RIGHTDOWN, MOUSEEVENTF_RIGHTUP),
    "middle": (MOUSEEVENTF_MIDDLEDOWN, MOUSEEVENTF_MIDDLEUP),
}


class InputInjectionError(Exception):
    """Raised when the platform refuses an injection call."""


def pointer_bounds(bounds: dict[str, int] | None = None) -> dict[str, int]:
    """Return a usable desktop rectangle, falling back to :data:`BOUNDS`."""
    if not bounds:
        return dict(BOUNDS)
    required = {"left", "top", "width", "height"}
    if not required.issubset(bounds) or bounds["width"] <= 0 or bounds["height"] <= 0:
        return dict(BOUNDS)
    return {key: int(bounds[key]) for key in required}


def unsupported_inputs(message: dict[str, Any], bounds: dict[str, int] | None = None) -> bool:
    """True when the event name is not one this agent knows how to replay."""
    return message.get("ev") not in INPUT_EVENTS


def _structure_defs() -> tuple[Any, Any, Any]:
    from ctypes import wintypes

    ulong_ptr = ctypes.c_ulonglong if ctypes.sizeof(ctypes.c_void_p) == 8 else ctypes.c_ulong

    class MOUSEINPUT(ctypes.Structure):
        _fields_ = [
            ("dx", wintypes.LONG),
            ("dy", wintypes.LONG),
            ("mouseData", wintypes.DWORD),
            ("dwFlags", wintypes.DWORD),
            ("time", wintypes.DWORD),
            ("dwExtraInfo", ulong_ptr),
        ]

    class KEYBDINPUT(ctypes.Structure):
        _fields_ = [
            ("wVk", wintypes.WORD),
            ("wScan", wintypes.WORD),
            ("dwFlags", wintypes.DWORD),
            ("time", wintypes.DWORD),
            ("dwExtraInfo", ulong_ptr),
        ]

    class INPUT(ctypes.Structure):
        class _INPUTUNION(ctypes.Union):
            _fields_ = [("mi", MOUSEINPUT), ("ki", KEYBDINPUT)]

        _anonymous_ = ("u",)
        _fields_ = [("type", wintypes.DWORD), ("u", _INPUTUNION)]

    return INPUT, MOUSEINPUT, KEYBDINPUT


def _send(inputs: list[Any]) -> None:
    if sys.platform != "win32":
        return
    user32 = ctypes.windll.user32
    array = (type(inputs[0]) * len(inputs))(*inputs)
    sent = user32.SendInput(len(inputs), array, ctypes.sizeof(type(inputs[0])))
    if sent != len(inputs):
        raise InputInjectionError("SendInput rejected the injected events")


def _mouse_input(flags: int, data: int = 0) -> Any:
    INPUT, MOUSEINPUT, _ = _structure_defs()
    item = INPUT()
    item.type = 0  # INPUT_MOUSE
    item.mi = MOUSEINPUT(0, 0, data, flags, 0, 0)
    return item


def _key_input(vk: int, flags: int) -> Any:
    INPUT, _, KEYBDINPUT = _structure_defs()
    item = INPUT()
    item.type = 1  # INPUT_KEYBOARD
    item.ki = KEYBDINPUT(vk, 0, flags, 0, 0)
    return item


def mouse_move(x: int, y: int) -> None:
    if sys.platform != "win32":
        return
    ctypes.windll.user32.SetCursorPos(int(x), int(y))


def mouse_button(button: str, down: bool) -> None:
    flags = _BUTTON_FLAGS.get(button)
    if flags is None:
        return
    _send([_mouse_input(flags[0] if down else flags[1])])


def mouse_wheel(delta: int) -> None:
    _send([_mouse_input(MOUSEEVENTF_WHEEL, int(delta))])


def key(vk: int, down: bool) -> None:
    _send([_key_input(int(vk), 0 if down else KEYEVENTF_KEYUP)])


def apply_input(
    message: dict[str, Any],
    bounds: dict[str, int] | None = None,
    *,
    move: Callable[[int, int], None] = mouse_move,
    button: Callable[[str, bool], None] = mouse_button,
    wheel: Callable[[int], None] = mouse_wheel,
    key: Callable[[int, bool], None] = key,
) -> bool:
    """Replay one ``input`` message. Returns True when the event was dispatched."""
    event = message.get("ev")
    if event not in INPUT_EVENTS:
        return False

    rect = pointer_bounds(bounds)

    if event == "mouse_move":
        try:
            xn = float(message["x"])
            yn = float(message["y"])
        except (KeyError, TypeError, ValueError):
            return False
        px, py = norm_to_pixel(xn, yn, rect["left"], rect["top"], rect["width"], rect["height"])
        move(px, py)
        return True

    if event in ("mouse_down", "mouse_up"):
        name = message.get("button")
        if name not in _BUTTON_FLAGS:
            return False
        button(name, event == "mouse_down")
        return True

    if event == "wheel":
        try:
            delta = int(message.get("delta", 0))
        except (TypeError, ValueError):
            return False
        delta = max(-MAX_WHEEL_DELTA, min(MAX_WHEEL_DELTA, delta))
        wheel(delta)
        return True

    if event in ("key_down", "key_up"):
        vk = keysym_to_vk(str(message.get("keysym", "")))
        if vk is None:
            return False
        key(vk, event == "key_down")
        return True

    return False
