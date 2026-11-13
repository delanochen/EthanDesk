"""Screen capture and JPEG encoding for the agent.

``encode_image`` is deliberately pure (Pillow only) so the encoding path can be tested
without a live desktop; :class:`ScreenCapturer` adds the mss grab and is created only by
the agent process.
"""

from __future__ import annotations

import io
from typing import Any

from PIL import Image

DEFAULT_QUALITY = 70
MIN_DIMENSION = 1


def _clamp_quality(quality: int) -> int:
    return max(1, min(95, int(quality)))


def _clamp_scale(scale: float) -> float:
    value = float(scale)
    if value != value or value <= 0.0:
        raise ValueError("scale must be greater than 0")
    return min(1.0, value)


def _resize(image: Image.Image, scale: float) -> Image.Image:
    if scale >= 1.0:
        return image
    width = max(MIN_DIMENSION, int(round(image.width * scale)))
    height = max(MIN_DIMENSION, int(round(image.height * scale)))
    return image.resize((width, height), Image.BILINEAR)


def encode_image(
    image: Image.Image,
    scale: float = 1.0,
    quality: int = DEFAULT_QUALITY,
) -> tuple[bytes, int, int]:
    """Encode a PIL image as JPEG, returning ``(payload, width, height)``."""
    factor = _clamp_scale(scale)
    resized = _resize(image, factor)
    if resized.mode not in ("RGB", "L"):
        resized = resized.convert("RGB")
    buffer = io.BytesIO()
    resized.save(buffer, format="JPEG", quality=_clamp_quality(quality), optimize=True)
    return buffer.getvalue(), resized.width, resized.height


def jpeg_size_estimate(
    image: Image.Image,
    scale: float = 1.0,
    quality: int = DEFAULT_QUALITY,
) -> int:
    return len(encode_image(image, scale=scale, quality=quality)[0])


class ScreenCapturer:
    """Grabs the virtual desktop with mss and encodes it for the viewer."""

    def __init__(self, monitor: int = 0) -> None:
        import mss  # imported lazily so tests never need a display

        self._sct = mss.mss()
        self._monitor_index = monitor

    @property
    def bounds(self) -> dict[str, int]:
        monitor = self._sct.monitors[self._monitor_index]
        return {
            "left": int(monitor["left"]),
            "top": int(monitor["top"]),
            "width": int(monitor["width"]),
            "height": int(monitor["height"]),
        }

    def capture(self, scale: float = 1.0, quality: int = DEFAULT_QUALITY) -> tuple[bytes, int, int]:
        monitor = self._sct.monitors[self._monitor_index]
        raw = self._sct.grab(monitor)
        image = Image.frombytes("RGB", raw.size, raw.bgra, "raw", "BGRX")
        return encode_image(image, scale=scale, quality=quality)

    def close(self) -> None:
        self._sct.close()
