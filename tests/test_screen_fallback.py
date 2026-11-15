"""抓屏兜底：桌面抓不到时（断开的 RDP 会话）改用逐窗口合成，桌面回来再切回去。"""

from __future__ import annotations

import io

import pytest
from PIL import Image

from agent.screen import ScreenCapturer


def _red_image(size=(320, 200)) -> Image.Image:
    return Image.new("RGB", size, (200, 30, 30))


def _blue_image(size=(320, 200)) -> Image.Image:
    return Image.new("RGB", size, (30, 30, 200))


def _tone(payload: bytes) -> str:
    """JPEG 有损，比颜色只看主通道就够判断帧是谁给的。"""
    red, _green, blue = Image.open(io.BytesIO(payload)).convert("RGB").getpixel((10, 10))
    return "red" if red > blue else "blue"


class _Desktop:
    """Fails the way mss does once the RDP session has no display surface."""

    def __init__(self, fail=True) -> None:
        self.fail = fail
        self.calls = 0

    def __call__(self) -> Image.Image:
        self.calls += 1
        if self.fail:
            raise OSError("Windows graphics function failed: BitBlt")
        return _red_image()


class _Composite:
    def __init__(self) -> None:
        self.calls = 0

    def grab(self) -> Image.Image:
        self.calls += 1
        return _blue_image()


def test_desktop_capture_is_preferred_when_it_works():
    desktop = _Desktop(fail=False)
    composite = _Composite()
    capturer = ScreenCapturer(desktop_grab=desktop, composite_grab=composite)

    payload, width, height = capturer.capture()

    assert (width, height) == (320, 200)
    assert _tone(payload) == "red"
    assert composite.calls == 0
    assert capturer.last_note == ""


def test_falls_back_to_window_composite_when_the_desktop_fails():
    desktop = _Desktop(fail=True)
    composite = _Composite()
    capturer = ScreenCapturer(desktop_grab=desktop, composite_grab=composite)

    payload, width, height = capturer.capture()

    assert (width, height) == (320, 200)
    assert _tone(payload) == "blue"
    assert composite.calls == 1
    assert capturer.last_note.startswith("桌面抓屏失败")


def test_failed_desktop_is_not_retried_on_every_frame():
    desktop = _Desktop(fail=True)
    composite = _Composite()
    capturer = ScreenCapturer(desktop_grab=desktop, composite_grab=composite, retry_seconds=1.0)

    for _ in range(5):
        capturer.capture()

    assert desktop.calls == 1
    assert composite.calls == 5


def test_desktop_capture_returns_after_the_cooldown():
    desktop = _Desktop(fail=True)
    composite = _Composite()
    capturer = ScreenCapturer(desktop_grab=desktop, composite_grab=composite, retry_seconds=0.0)
    capturer.capture()
    assert capturer.last_note.startswith("桌面抓屏失败")

    desktop.fail = False
    payload, _, _ = capturer.capture()

    assert _tone(payload) == "red"
    assert capturer.last_note == ""


def test_without_a_composite_source_the_desktop_error_is_raised():
    desktop = _Desktop(fail=True)
    capturer = ScreenCapturer(desktop_grab=desktop, composite_grab=False)

    with pytest.raises(OSError):
        capturer.capture()
