"""真机验收：主控端断线重连。

跑法和 probe_worker 一样（登录 → 开会话 → 收帧 → 回 frame_recv），区别在于它把
"会话掉了又自己接回来"当作成功条件：至少两次 session_open，且第二次之后还能收到帧。

用法：python probe_reconnect.py [秒数，默认 90]
被控端：ETHANDESK_AGENT_ID / ETHANDESK_SESSION_CODE；口令 ETHANDESK_RELAY_PASSWORD。
"""

from __future__ import annotations

import os
import queue
import sys
import time

from common.defaults import DEFAULT_VIEWER_URL
from viewer.app import ViewerWorker

PASSWORD = os.environ.get("ETHANDESK_RELAY_PASSWORD", "")


def main() -> None:
    seconds = float(sys.argv[1]) if len(sys.argv) > 1 else 90.0
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

    started = time.time()
    deadline = started + seconds
    connection_opens: list[float] = []
    frames_after_reopen = 0
    frames = 0
    logs: list[str] = []
    errors: list[str] = []
    reconnected_at: float | None = None
    logged_in = False

    while time.time() < deadline:
        try:
            kind, payload = events.get(timeout=0.2)
        except queue.Empty:
            continue
        if kind == "logged_in":
            logged_in = True
            commands.put(("connect", {"agent_id": agent_id, "code": code}))
            commands.put(("auto_quality", True))
        elif kind == "session_open":
            connection_opens.append(time.time() - started)
            print(f"[{time.time() - started:5.1f}s] session_open {payload}", flush=True)
            if len(connection_opens) >= 2 and reconnected_at is None:
                reconnected_at = time.time()
        elif kind == "frame":
            frames += 1
            if reconnected_at is not None:
                frames_after_reopen += 1
            commands.put(("frame_recv", None))
        elif kind == "log":
            logs.append(f"[{time.time() - started:5.1f}s] {payload}")
            print(f"log: {logs[-1]}", flush=True)
        elif kind == "status":
            print(f"status: [{time.time() - started:5.1f}s] {payload}", flush=True)
        elif kind == "error":
            errors.append(str(payload))
            print(f"error: [{time.time() - started:5.1f}s] {payload}", flush=True)

    worker.stop()
    elapsed = max(0.001, time.time() - started)
    print(f"frames={frames} elapsed={elapsed:.1f}s opens={len(connection_opens)} "
          f"after_reopen_frames={frames_after_reopen}", flush=True)
    for entry in logs[-8:]:
        print(f"log: {entry}", flush=True)
    if errors:
        print(f"errors={errors[:4]}", flush=True)
    ok = logged_in and len(connection_opens) >= 2 and frames_after_reopen > 0
    print("RESULT ok" if ok else "RESULT failed", flush=True)


if __name__ == "__main__":
    main()
