"""真机验收：把客户端真正用的 ViewerWorker 拉起来，连一台指定设备跑 N 秒。

和 GUI 走的是同一条路（登录 → 开会话 → 收帧 → 回 frame_recv → 自适应画质循环），
所以它跑通就等于远控主链路在真机上跑通；它统计的帧率、首帧尺寸和自适应事件
也直接对应主控端看到的东西。

用法：python probe_worker.py [秒数，默认 20]
被控端：ETHANDESK_AGENT_ID / ETHANDESK_SESSION_CODE 指定；口令 ETHANDESK_RELAY_PASSWORD。
"""

from __future__ import annotations

import os
import queue
import sys
import time

from common.defaults import DEFAULT_VIEWER_URL
from viewer.app import ViewerWorker

# 口令不进仓库：跑之前设 ETHANDESK_RELAY_PASSWORD。
PASSWORD = os.environ.get("ETHANDESK_RELAY_PASSWORD", "")


def main() -> None:
    seconds = float(sys.argv[1]) if len(sys.argv) > 1 else 20.0
    agent_id = os.environ.get("ETHANDESK_AGENT_ID", "")
    code = os.environ.get("ETHANDESK_SESSION_CODE", "")
    if not agent_id:
        print("RESULT failed 缺少 ETHANDESK_AGENT_ID", flush=True)
        raise SystemExit(2)

    events: "queue.Queue" = queue.Queue()
    commands: "queue.Queue" = queue.Queue()
    worker = ViewerWorker(events, commands)
    worker.start()

    commands.put(("login", {"url": DEFAULT_VIEWER_URL, "password": PASSWORD}))
    deadline = time.time() + seconds
    frames = 0
    first_size = None
    bytes_total = 0
    adapt_events: list[dict] = []
    logs: list[str] = []
    errors: list[str] = []
    connected = False
    started = time.time()

    # 登录之后才连会话：登录失败时直接报出来，不要盲等。
    login_deadline = time.time() + 10.0
    while time.time() < login_deadline and not connected:
        try:
            kind, payload = events.get(timeout=0.5)
        except queue.Empty:
            continue
        if kind == "error":
            errors.append(str(payload))
            print(f"RESULT failed login: {payload}", flush=True)
            worker.stop()
            return
        if kind == "logged_in":
            commands.put(("connect", {"agent_id": agent_id, "code": code}))
            commands.put(("auto_quality", True))
            break

    while time.time() < deadline:
        try:
            kind, payload = events.get(timeout=0.2)
        except queue.Empty:
            continue
        if kind == "frame":
            frames += 1
            bytes_total += len(payload["data"])
            if first_size is None:
                first_size = (payload["header"].get("width"), payload["header"].get("height"))
            # 主控端必须回 frame_recv，否则 agent 的在途窗口（MAX_INFLIGHT）会打满。
            commands.put(("frame_recv", None))
        elif kind == "session_open":
            connected = True
            print(f"session_open {payload}", flush=True)
        elif kind == "auto_profile":
            adapt_events.append(payload)
        elif kind == "log":
            logs.append(str(payload))
        elif kind == "error":
            errors.append(str(payload))

    elapsed = max(0.001, time.time() - started)
    worker.stop()
    fps = frames / elapsed
    print(f"frames={frames} elapsed={elapsed:.1f}s fps={fps:.2f} first={first_size} "
          f"avg={int(bytes_total / frames) if frames else 0}B", flush=True)
    for entry in logs[-6:]:
        print(f"log: {entry}", flush=True)
    for entry in adapt_events[-4:]:
        print(f"auto_profile: {entry}", flush=True)
    if errors:
        print(f"errors={errors[:4]}", flush=True)
    ok = connected and frames > 0 and not errors
    print("RESULT ok" if ok else "RESULT failed", flush=True)


if __name__ == "__main__":
    main()
