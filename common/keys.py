"""Tk keysym to Windows virtual-key translation.

The viewer receives Tk keysyms (``"a"``, ``"Return"``, ``"Shift_L"`` ...) and the agent
needs virtual-key codes for ``SendInput``. Unknown keysyms return ``None`` so the agent
can ignore them rather than inject a wrong key.
"""

from __future__ import annotations

_NAMED: dict[str, int] = {
    "Return": 0x0D,
    "KP_Enter": 0x0D,
    "Escape": 0x1B,
    "Tab": 0x09,
    "BackSpace": 0x08,
    "space": 0x20,
    "Delete": 0x2E,
    "Insert": 0x2D,
    "Home": 0x24,
    "End": 0x23,
    "Prior": 0x21,
    "Next": 0x22,
    "Left": 0x25,
    "Up": 0x26,
    "Right": 0x27,
    "Down": 0x28,
    "Shift_L": 0xA0,
    "Shift_R": 0xA1,
    "Control_L": 0xA2,
    "Control_R": 0xA3,
    "Alt_L": 0xA4,
    "Alt_R": 0xA5,
    "Super_L": 0x5B,
    "Super_R": 0x5C,
    "Caps_Lock": 0x14,
    "Num_Lock": 0x90,
    "Scroll_Lock": 0x91,
    "Print": 0x2C,
    "Pause": 0x13,
}

_PUNCTUATION: dict[str, int] = {
    "period": 0xBE,
    "comma": 0xBC,
    "minus": 0xBD,
    "equal": 0xBB,
    "slash": 0xBF,
    "backslash": 0xDC,
    "semicolon": 0xBA,
    "colon": 0xBA,
    "apostrophe": 0xDE,
    "quotedbl": 0xDE,
    "bracketleft": 0xDB,
    "braceleft": 0xDB,
    "bracketright": 0xDD,
    "braceright": 0xDD,
    "grave": 0xC0,
    "asciitilde": 0xC0,
    "less": 0xBC,
    "greater": 0xBE,
    "question": 0xBF,
    "underscore": 0xBD,
    "plus": 0xBB,
    "bar": 0xDC,
    "exclam": 0x31,
    "at": 0x32,
    "numbersign": 0x33,
    "dollar": 0x34,
    "percent": 0x35,
    "asciicircum": 0x36,
    "ampersand": 0x37,
    "asterisk": 0x38,
    "parenleft": 0x39,
    "parenright": 0x30,
}

# Shifted symbols map to the base digit key on purpose: the viewer also sends the
# modifier state, so holding Shift produces the shifted character on the agent side.
_SHIFTED_DIGITS: dict[str, int] = {
    "exclam": 0x31,
    "at": 0x32,
    "numbersign": 0x33,
    "dollar": 0x34,
    "percent": 0x35,
    "asciicircum": 0x36,
    "ampersand": 0x37,
    "asterisk": 0x38,
    "parenleft": 0x39,
    "parenright": 0x30,
}


def keysym_to_vk(keysym: str) -> int | None:
    """Translate a Tk keysym into a Win32 virtual-key code."""
    if not keysym:
        return None

    if len(keysym) == 1:
        char = keysym
        if "a" <= char <= "z":
            return ord(char.upper())
        if "A" <= char <= "Z":
            return ord(char)
        if "0" <= char <= "9":
            return ord(char)

    if keysym in _NAMED:
        return _NAMED[keysym]
    if keysym in _PUNCTUATION:
        return _PUNCTUATION[keysym]
    if keysym in _SHIFTED_DIGITS:
        return _SHIFTED_DIGITS[keysym]

    if len(keysym) > 1 and keysym[0] == "F" and keysym[1:].isdigit():
        index = int(keysym[1:])
        if 1 <= index <= 24:
            return 0x70 + index - 1

    if len(keysym) == 4 and keysym.startswith("KP_") and keysym[3].isdigit():
        return 0x60 + int(keysym[3])

    return None
