"""The agent keeps a few frames in flight so a slow link is not capped at one frame per RTT."""

from __future__ import annotations

import asyncio
import queue

from agent.app import MAX_INFLIGHT, AgentWorker
from agent.config import AgentConfig


def _worker() -> AgentWorker:
    return AgentWorker(AgentConfig(), queue.Queue(), queue.Queue())


def test_pipeline_allows_more_than_one_frame_in_flight():
    assert MAX_INFLIGHT >= 2


def test_frame_ack_releases_exactly_one_slot():
    worker = _worker()
    worker._inflight = MAX_INFLIGHT

    asyncio.run(worker._handle_text(None, {"t": "frame_recv"}))

    assert worker._inflight == MAX_INFLIGHT - 1


def test_frame_ack_never_goes_negative():
    worker = _worker()
    worker._inflight = 1

    asyncio.run(worker._handle_text(None, {"t": "frame_recv"}))
    asyncio.run(worker._handle_text(None, {"t": "frame_recv"}))

    assert worker._inflight == 0
