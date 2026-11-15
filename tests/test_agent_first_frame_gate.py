"""首帧门闩：主控端下发的画面档位必须先到，再发第一帧。"""

from __future__ import annotations

import asyncio
import queue

from agent.app import SETTINGS_GRACE, AgentWorker
from agent.config import AgentConfig


def _worker() -> AgentWorker:
    return AgentWorker(AgentConfig(), queue.Queue(), queue.Queue())


def test_set_quality_releases_the_first_frame_gate():
    worker = _worker()
    gate = asyncio.Event()
    worker._settings_event = gate
    asyncio.run(worker._handle_text(None, {"t": "set_quality", "quality": 45, "scale": 0.4, "fps": 10}))
    assert gate.is_set()
    assert worker._quality == 45
    assert worker._scale == 0.4


def test_gate_is_bounded_so_a_silent_viewer_still_gets_a_frame():
    """没有 set_quality 时最多等 SETTINGS_GRACE，不能把首帧无限期扣着。"""
    assert 0 < SETTINGS_GRACE <= 3.0


def test_session_start_replaces_the_gate():
    worker = _worker()

    async def start():
        await worker._on_session_start(None)

    asyncio.run(start())
    assert worker._settings_event is not None
    assert not worker._settings_event.is_set()


def test_viewer_profile_is_remembered_for_the_next_session():
    """跨洋链路上首帧必须沿用上次的档位，否则 1.0×/q70 会先把链路堵死。"""
    worker = _worker()
    worker.config.scale = 1.0
    worker.config.quality = 70
    worker._scale, worker._quality, worker._fps = 1.0, 70, 15
    worker._settings_event = asyncio.Event()
    asyncio.run(worker._handle_text(None, {"t": "set_quality", "quality": 45, "scale": 0.4, "fps": 10}))
    assert worker._remote_profile == (0.4, 45, 10)

    worker._scale, worker._quality, worker._fps = 1.0, 70, 15
    worker._start_capture = lambda _ws: None  # 不真的起抓屏任务
    asyncio.run(worker._on_session_start(None))
    assert (worker._scale, worker._quality, worker._fps) == (0.4, 45, 10)
