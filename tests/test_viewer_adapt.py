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


def test_slow_link_drops_quality_before_resolution():
    scale, quality = next_video_profile(0.5, 1.0, 70, 1.0, 70)
    assert quality == 60
    assert scale == 1.0


def test_quality_floor_then_scale_drops():
    scale, quality = next_video_profile(0.0, 1.0, ADAPT_MIN_QUALITY, 1.0, 70)
    assert quality == ADAPT_MIN_QUALITY
    assert scale == 0.95


def test_never_below_scale_floor():
    scale, quality = next_video_profile(0.0, ADAPT_MIN_SCALE, ADAPT_MIN_QUALITY, 1.0, 70)
    assert scale == ADAPT_MIN_SCALE
    assert quality == ADAPT_MIN_QUALITY


def test_dead_link_stops_at_floors():
    """No frames at all: repeated windows must park at the floor, not go negative."""
    scale, quality = ADAPT_MIN_SCALE, ADAPT_MIN_QUALITY
    for _ in range(20):
        scale, quality = next_video_profile(0.0, scale, quality, 1.0, 70)
    assert (scale, quality) == (ADAPT_MIN_SCALE, ADAPT_MIN_QUALITY)


def test_fast_link_climbs_scale_back_first():
    scale, quality = next_video_profile(ADAPT_HIGH_FPS, 0.6, ADAPT_MIN_QUALITY, 1.0, 70)
    assert scale == 0.65
    assert quality == ADAPT_MIN_QUALITY


def test_fast_link_then_raises_quality_to_ceiling():
    scale, quality = next_video_profile(ADAPT_HIGH_FPS, 1.0, 60, 1.0, 70)
    assert scale == 1.0
    assert quality == 70


def test_never_exceeds_user_profile():
    scale, quality = next_video_profile(ADAPT_HIGH_FPS, 1.0, 70, 1.0, 70)
    assert (scale, quality) == (1.0, 70)


def test_quality_ceiling_is_capped_at_85():
    scale, quality = next_video_profile(ADAPT_HIGH_FPS, 1.0, 85, 1.0, 95)
    assert quality == ADAPT_MAX_QUALITY


def test_mid_band_is_left_alone():
    """Between the low and high thresholds a marginal link must not oscillate."""
    midpoint = (ADAPT_LOW_FPS + ADAPT_HIGH_FPS) / 2
    assert next_video_profile(midpoint, 0.8, 55, 1.0, 70) == (0.8, 55)


def test_low_profile_floor_follows_base():
    """A user who already asked for 0.4× is not pushed below their own setting."""
    scale, quality = next_video_profile(0.0, 0.4, 35, 0.4, 45)
    assert scale == 0.4
    assert quality == 35


def test_worker_sends_base_profile_then_adapts_it():
    worker_session = queue.Queue()
    worker = __import__("viewer.app", fromlist=["ViewerWorker"]).ViewerWorker(
        queue.Queue(), worker_session
    )
    stub = _StubSession()
    worker._session = stub
    asyncio.run(worker._dispatch(("settings", {"quality": 70, "scale": 1.0, "fps": 15})))
    assert _quality_messages(stub)[-1] == {"t": "set_quality", "scale": 1.0, "quality": 70, "fps": 15}

    asyncio.run(worker._dispatch(("auto_quality", True)))
    assert worker._auto_quality is True

    asyncio.run(worker._dispatch(("settings", {"quality": 60, "scale": 1.0, "fps": 15})))
    assert _quality_messages(stub)[-1]["quality"] == 60


def test_worker_turning_auto_off_restores_user_profile():
    worker = __import__("viewer.app", fromlist=["ViewerWorker"]).ViewerWorker(
        queue.Queue(), queue.Queue()
    )
    stub = _StubSession()
    worker._session = stub
    asyncio.run(worker._dispatch(("settings", {"quality": 70, "scale": 1.0, "fps": 15})))
    asyncio.run(worker._dispatch(("auto_quality", True)))
    worker._eff_scale, worker._eff_quality = ADAPT_MIN_SCALE, ADAPT_MIN_QUALITY
    asyncio.run(worker._dispatch(("auto_quality", False)))
    assert (worker._eff_scale, worker._eff_quality) == (1.0, 70)
    assert _quality_messages(stub)[-1]["quality"] == 70


def test_worker_flips_auto_even_without_session():
    worker = __import__("viewer.app", fromlist=["ViewerWorker"]).ViewerWorker(
        queue.Queue(), queue.Queue()
    )
    asyncio.run(worker._dispatch(("auto_quality", True)))
    assert worker._auto_quality is True


def test_upload_accepts_dict_and_plain_path(tmp_path):
    worker = __import__("viewer.app", fromlist=["ViewerWorker"]).ViewerWorker(
        queue.Queue(), queue.Queue()
    )
    stub = _StubSession()
    worker._session = stub
    sample = tmp_path / "note.txt"
    sample.write_text("hello", encoding="utf-8")
    asyncio.run(worker._dispatch(("upload", {"path": str(sample)})))
    assert any(m.get("t") == "file_offer" for m in stub.sent)
