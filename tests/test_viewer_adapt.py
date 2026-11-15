"""Adaptive picture control: the decision function and how the worker wires it up."""

from __future__ import annotations

import asyncio
import queue

from viewer.app import (
    ADAPT_HIGH_FPS,
    ADAPT_LOW_FPS,
    ADAPT_MAX_QUALITY,
    ADAPT_MIN_QUALITY,
    ADAPT_MIN_SCALE,
    ViewerWorker,
    next_video_profile,
)


class _StubSession:
    def __init__(self) -> None:
        self.sent: list[dict] = []

    async def send_text(self, message: dict) -> None:
        self.sent.append(message)

    async def close(self) -> None:
        return None


def _quality_messages(session: _StubSession) -> list[dict]:
    return [m for m in session.sent if m.get("t") == "set_quality"]


def _worker() -> ViewerWorker:
    return ViewerWorker(queue.Queue(), queue.Queue())


def test_slow_link_drops_scale_and_quality_together():
    updated = next_video_profile({"quality": 70, "scale": 1.0, "fps": 15}, fps=1.2)
    assert updated is not None
    assert updated["scale"] == 0.85
    assert updated["quality"] == 62


def test_scale_never_goes_below_the_floor():
    profile = {"quality": ADAPT_MIN_QUALITY, "scale": ADAPT_MIN_SCALE + 0.05, "fps": 15}
    updated = next_video_profile(profile, fps=0.5)
    assert updated is not None
    assert updated["scale"] == ADAPT_MIN_SCALE


def test_dead_link_parks_at_the_floor():
    """No frames at all: repeated windows must park, not go negative."""
    profile = {"quality": 70, "scale": 1.0, "fps": 15}
    for _ in range(20):
        updated = next_video_profile(profile, fps=0.0)
        if updated is None:
            break
        profile = updated
    assert profile["scale"] == ADAPT_MIN_SCALE
    assert profile["quality"] >= ADAPT_MIN_QUALITY
    assert next_video_profile(profile, fps=0.0) is None


def test_fast_link_climbs_scale_back():
    updated = next_video_profile({"quality": 45, "scale": 0.6, "fps": 15}, fps=15.0)
    assert updated is not None
    assert updated["scale"] == 0.7
    assert updated["quality"] == 50


def test_quality_ceiling_is_capped_at_85():
    updated = next_video_profile({"quality": 84, "scale": 0.95, "fps": 15}, fps=15.0)
    assert updated is not None
    assert updated["quality"] == ADAPT_MAX_QUALITY


def test_mid_band_is_left_alone():
    """Between the low and high thresholds a marginal link must not oscillate."""
    midpoint = (ADAPT_LOW_FPS + ADAPT_HIGH_FPS) / 2
    assert next_video_profile({"quality": 55, "scale": 0.8, "fps": 15}, fps=midpoint) is None


def test_worker_sends_the_cached_profile_on_connect():
    worker = _worker()
    worker._remember_settings({"quality": 55, "scale": 0.75, "fps": 10})
    stub = _StubSession()
    worker._session = stub
    asyncio.run(worker._dispatch(("settings", {"quality": 55, "scale": 0.75, "fps": 10})))
    assert _quality_messages(stub)[-1] == {
        "t": "set_quality", "quality": 55, "scale": 0.75, "fps": 10
    }


def test_manual_settings_switch_auto_quality_off():
    worker = _worker()
    asyncio.run(worker._dispatch(("settings", {"quality": 60, "scale": 0.8, "fps": 10})))
    assert worker._auto_quality is False
    assert worker._video == {"quality": 60, "scale": 0.8, "fps": 10}


def test_worker_flips_auto_even_without_session():
    worker = _worker()
    asyncio.run(worker._dispatch(("auto_quality", True)))
    assert worker._auto_quality is True


def test_delivered_frames_are_counted_for_the_adapt_window():
    """The loop must read frames actually delivered; a counter that never moves reads as 0 fps."""
    worker = _worker()

    class _Pairer:
        def attach(self, data):
            return {"kind": "frame"}, b"jpeg"

    worker._pairer = _Pairer()
    for _ in range(3):
        worker._handle_binary(b"x")
    assert worker._frames_window == 3


def test_upload_accepts_dict_and_plain_path(tmp_path):
    worker = _worker()
    stub = _StubSession()
    worker._session = stub
    sample = tmp_path / "note.txt"
    sample.write_text("hello", encoding="utf-8")
    asyncio.run(worker._dispatch(("upload", {"path": str(sample)})))
    assert any(m.get("t") == "file_offer" for m in stub.sent)
