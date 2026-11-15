"""Windows DPI awareness: capture, input and widgets must share one coordinate space.

``mss`` grabs physical pixels (1920x1080 on a 125% display) while a DPI-unaware
process sees a logical desktop of 1536x864. Two consequences: ``SetCursorPos`` lands
at 80% of the point the viewer clicked, and Tk lays itself out at a fraction of the
screen. Becoming per-monitor DPI aware before the first capture or window puts both
halves in the physical space; :func:`tk_scaling` then keeps widget sizes readable.
"""

from __future__ import annotations

import sys

# DPI_AWARENESS_CONTEXT_PER_MONITOR_AWARE_V2
PER_MONITOR_AWARE_V2 = -4
# PROCESS_PER_MONITOR_DPI_AWARE
_PROCESS_PER_MONITOR = 2


def ensure_dpi_aware() -> bool:
    """Make this process per-monitor DPI aware. Idempotent; False when unavailable."""
    if sys.platform != "win32":
        return False
    import ctypes

    try:
        user32 = ctypes.windll.user32
        handle = getattr(user32, "SetProcessDpiAwarenessContext", None)
        if handle is not None and handle(ctypes.c_void_p(PER_MONITOR_AWARE_V2)):
            return True
    except (AttributeError, OSError, ValueError):
        pass
    try:
        ctypes.windll.shcore.SetProcessDpiAwareness(_PROCESS_PER_MONITOR)
        return True
    except (AttributeError, OSError, ValueError):
        pass
    try:
        return bool(ctypes.windll.user32.SetProcessDPIAware())
    except (AttributeError, OSError, ValueError):
        return False


def system_dpi() -> int:
    """The desktop DPI, 96 when unknown."""
    if sys.platform != "win32":
        return 96
    import ctypes

    try:
        return int(ctypes.windll.user32.GetDpiForSystem())
    except (AttributeError, OSError, ValueError):
        return 96


def tk_scaling(root) -> float:
    """Apply Tk's pixels-per-point scaling for the current DPI and return it."""
    scaling = system_dpi() / 72.0
    try:
        root.tk.call("tk", "scaling", scaling)
    except Exception:
        pass
    return scaling
