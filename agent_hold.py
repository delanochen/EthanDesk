"""无头被控端 runner：不起 Tk，直接把 AgentWorker 跑起来并打印事件。

用法：python agent_hold.py [存活秒数，默认 0 = 一直跑]
"""

from __future__ import annotations

import queue
import sys
import threading
import time

from agent.app import AgentWorker
from agent.config import AgentConfig
from common.dpi import ensure_dpi_aware

ensure_dpi_aware()


def main() -> None:
    seconds = float(sys.argv[1]) if len(sys.argv) > 1 else 0.0
    config = AgentConfig.load()
    events: "queue.Queue" = queue.Queue()
    commands: "queue.Queue" = queue.Queue()
    worker = AgentWorker(config, events, commands)
    worker.start()
    print(
        f"[agent_hold] relay={config.relay_url} id={config.agent_id} "
        f"name={config.name} unattended={config.unattended}",
        flush=True,
    )
    started = time.time()
    try:
        while seconds <= 0 or time.time() - started < seconds:
            try:
                kind, payload = events.get(timeout=0.5)
            except queue.Empty:
                continue
            stamp = time.strftime("%H:%M:%S")
            print(f"[{stamp}] {kind}: {payload}", flush=True)
    except KeyboardInterrupt:
        pass
    finally:
        worker.stop()
        threading.Event().wait(1.0)


if __name__ == "__main__":
    main()
