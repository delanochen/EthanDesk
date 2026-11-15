"""Screen capture and JPEG encoding for the agent.

``encode_image`` is deliberately pure (Pillow only) so the encoding path can be tested
without a live desktop; :class:`ScreenCapturer` adds the mss grab and is created only by
the agent process.
"""

from __future__ import annotations

import io
import os
import time
from typing import Any

from PIL import Image

DEFAULT_QUALITY = 70
MIN_DIMENSION = 1
# 桌面抓屏失败后，隔这么久再试一次：RDP 会话重新连上就能回到真抓屏。
DESKTOP_RETRY_SECONDS = 15.0


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
    """Grabs the virtual desktop, and composes windows when there is no framebuffer.

    ``mss`` reads the desktop through ``BitBlt``, which fails once the session has no
    attached display surface (a disconnected RDP session, a locked console). Individual
    windows keep painting themselves, so the fallback renders each top-level window
    into the frame instead of sending nothing at all. The desktop path is retried
    periodically, so a session that comes back to life returns to real capture.
    """

    def __init__(
        self,
        monitor: int = 0,
        *,
        desktop_grab: Any = None,
        composite_grab: Any = None,
        retry_seconds: float = DESKTOP_RETRY_SECONDS,
    ) -> None:
        self._monitor_index = monitor
        self._sct = None
        self._desktop_grab = desktop_grab
        self._composite = composite_grab
        self._retry_seconds = retry_seconds
        self._desktop_blocked_until = 0.0
        self.last_note = ""

    # ------------------------------------------------------------------ sources
    def _desktop_image(self) -> Image.Image:
        if self._desktop_grab is not None:
            return self._desktop_grab()
        if self._sct is None:
            import mss  # imported lazily so tests never need a display

            self._sct = mss.mss()
        monitor = self._sct.monitors[self._monitor_index]
        raw = self._sct.grab(monitor)
        return Image.frombytes("RGB", raw.size, raw.bgra, "raw", "BGRX")

    def _composite_image(self) -> Image.Image | None:
        if self._composite is False:  # 显式关掉兜底（测试和一些嵌入式场景用）
            return None
        if self._composite is None:
            if os.name != "nt":
                return None
            from agent.win_capture import WindowCompositeCapturer

            self._composite = WindowCompositeCapturer()
        grab = getattr(self._composite, "grab", self._composite)
        return grab()

    def _composite_bounds(self) -> dict[str, int] | None:
        if self._desktop_grab is not None or os.name != "nt":
            return None
        from agent.win_capture import virtual_screen

        left, top, width, height = virtual_screen()
        return {"left": left, "top": top, "width": width, "height": height}

    # -------------------------------------------------------------------- public
    @property
    def bounds(self) -> dict[str, int]:
        try:
            if self._sct is None:
                import mss

                self._sct = mss.mss()
            monitor = self._sct.monitors[self._monitor_index]
        except Exception:
            composed = self._composite_bounds()
            if composed is None:
                raise
            return composed
        return {
            "left": int(monitor["left"]),
            "top": int(monitor["top"]),
            "width": int(monitor["width"]),
            "height": int(monitor["height"]),
        }

    def capture(self, scale: float = 1.0, quality: int = DEFAULT_QUALITY) -> tuple[bytes, int, int]:
        if time.monotonic() < self._desktop_blocked_until:
            image = self._composite_image()
            if image is None:
                raise RuntimeError("桌面抓屏失败且没有窗口合成兜底")
            return encode_image(image, scale=scale, quality=quality)
        try:
            payload = encode_image(self._desktop_image(), scale=scale, quality=quality)
        except Exception as error:
            self._desktop_blocked_until = time.monotonic() + self._retry_seconds
            image = self._composite_image()
            if image is None:
                raise
            self.last_note = f"桌面抓屏失败，改用窗口合成抓屏：{error}"
            return encode_image(image, scale=scale, quality=quality)
        self.last_note = ""
        return payload

    def close(self) -> None:
        if self._sct is not None:
            self._sct.close()
            self._sct = None
