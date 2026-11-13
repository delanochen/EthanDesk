"""Coordinate conversions between the viewer canvas, virtual desktop pixels and Win32
absolute units.

The viewer always talks in normalised coordinates (0..1 over the captured frame) so the
protocol stays resolution independent. The agent converts them against the virtual
desktop rectangle, which may start at a negative origin on multi-monitor setups.
"""

from __future__ import annotations


def _clamp01(value: float) -> float:
    if value != value:  # NaN
        return 0.0
    return 0.0 if value < 0.0 else 1.0 if value > 1.0 else float(value)


def _require_size(width: int, height: int) -> None:
    if width <= 0 or height <= 0:
        raise ValueError("screen size must be positive")


def norm_to_pixel(
    xn: float,
    yn: float,
    left: int,
    top: int,
    width: int,
    height: int,
) -> tuple[int, int]:
    """Map normalised coordinates onto a pixel inside the virtual desktop."""
    _require_size(width, height)
    px = left + round(_clamp01(xn) * (width - 1))
    py = top + round(_clamp01(yn) * (height - 1))
    return int(px), int(py)


def pixel_to_norm(
    x: int,
    y: int,
    left: int,
    top: int,
    width: int,
    height: int,
) -> tuple[float, float]:
    """Inverse of :func:`norm_to_pixel`, clamped into 0..1."""
    _require_size(width, height)
    nx = (x - left) / (width - 1)
    ny = (y - top) / (height - 1)
    return _clamp01(nx), _clamp01(ny)


def norm_to_absolute_units(xn: float, yn: float) -> tuple[int, int]:
    """Normalised coordinates to the 0..65535 range used by MOUSEEVENTF_ABSOLUTE."""
    return round(_clamp01(xn) * 65535), round(_clamp01(yn) * 65535)


def letterbox_fit(
    frame_width: int,
    frame_height: int,
    canvas_width: int,
    canvas_height: int,
) -> tuple[int, int, int, int]:
    """Return ``(offset_x, offset_y, draw_width, draw_height)`` that fits a frame inside
    a canvas without distortion."""
    if frame_width <= 0 or frame_height <= 0 or canvas_width <= 0 or canvas_height <= 0:
        return 0, 0, 0, 0
    scale = min(canvas_width / frame_width, canvas_height / frame_height)
    draw_w = max(1, int(frame_width * scale))
    draw_h = max(1, int(frame_height * scale))
    return (canvas_width - draw_w) // 2, (canvas_height - draw_h) // 2, draw_w, draw_h


def canvas_to_frame_norm(
    canvas_x: float,
    canvas_y: float,
    frame_width: int,
    frame_height: int,
    canvas_width: int,
    canvas_height: int,
) -> tuple[float, float]:
    """Convert a canvas click into normalised frame coordinates, honouring letterboxing."""
    offset_x, offset_y, draw_w, draw_h = letterbox_fit(
        frame_width, frame_height, canvas_width, canvas_height
    )
    if draw_w <= 0 or draw_h <= 0:
        return 0.0, 0.0
    xn = (canvas_x - offset_x) / draw_w
    yn = (canvas_y - offset_y) / draw_h
    return _clamp01(xn), _clamp01(yn)
