"""真机验：刚连上就撞 agent_busy 时，主控端自己等被控端收尾再试。

场景：被控端上一个会话还没收尾（另一个主控端刚断、或它自己的窗口刚关），
这时候点「连接」中继会回 agent_busy。要的是自动等几秒重试，而不是甩一句错误
让用户再点一次。

用法：python probe_busy.py [秒数，默认 30]
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
    seconds = float(sys.argv[1]) if len(sys.argv) > 1 else 30.0
    agent_id = os.environ.get("ETHANDESK_AGENT_ID", "")
    code = os.environ.get("ETHANDESK_SESSION_CODE", "")
    if not agent_id:
        print("RESULT failed 缺少 ETHANDESK_AGENT_ID", flush=True)
        raise SystemExit(2)

    events: "queue.Queue" = queue.Queue()
    commands: "queue.Queue" = queue.Queue()
    worker = ViewerWorker(events, commands)
    worker.start()

    started = time.time()
    deadline = started + seconds
    retries: list[str] = []
    opened: list[float] = []
    frames = 0
    errors: list[str] = []
    logged_in = False

    commands.put(("login", {"url": DEFAULT_VIEWER_URL, "password": PASSWORD}))

    while time.time() < deadline:
        try:
            kind, payload = events.get(timeout=0.2)
        except queue.Empty:
            continue
        if kind == "logged_in":
            logged_in = True
            print(f"[{time.time() - started:5.1f}s] logged_in", flush=True)
            commands.put(("connect", {"agent_id": agent_id, "code": code}))
        elif kind == "status":
            text = str(payload)
            print(f"[{time.time() - started:5.1f}s] status {text}", flush=True)
            if "重试" in text:
                retries.append(text)
        elif kind == "session_open":
            opened.append(time.time() - started)
            print(f"[{time.time() - started:5.1f}s] session_open {payload}", flush=True)
            commands.put(("auto_quality", True))
        elif kind == "frame":
            frames += 1
            commands.put(("frame_recv", None))
        elif kind == "error":
            errors.append(str(payload))
            print(f"[{time.time() - started:5.1f}s] error {payload}", flush=True)
        elif kind == "log":
            print(f"[{time.time() - started:5.1f}s] log {payload}", flush=True)
            if "重试" in str(payload):
                retries.append(str(payload))
        if opened and frames >= 3:
            break

    worker.stop()
    print(f"retries={len(retries)} opened={len(opened)} frames={frames} errors={errors[:3]}", flush=True)
    ok = logged_in and bool(opened) and frames > 0
    if ok and not retries:
        # 没撞上 busy 说明这次没验到想验的东西，别报 ok 骗自己。
        print("RESULT inconclusive 没撞上 agent_busy", flush=True)
        return
    print("RESULT ok" if ok else "RESULT failed", flush=True)


if __name__ == "__main__":
    main()
