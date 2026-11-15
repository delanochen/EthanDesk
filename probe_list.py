"""真机验“远程目录浏览”：主控端请求列被控端某个目录，被控端回 meta/file_list。

跟主控端「远程文件…」窗口走同一条协议（登录 → 开会话 → file_offer/list → 收条目），
所以条目对得上就等于窗口里能点着走。

用法：python probe_list.py [被控端目录，留空表示顶层盘符] [最多等待秒数，默认 15]
被控端：ETHANDESK_AGENT_ID / ETHANDESK_SESSION_CODE；口令 ETHANDESK_RELAY_PASSWORD。
"""

from __future__ import annotations

import asyncio
import os
import sys
import time

from common.defaults import DEFAULT_VIEWER_URL
from viewer.client import DeskClient

PASSWORD = os.environ.get("ETHANDESK_RELAY_PASSWORD", "")
URL = os.environ.get("ETHANDESK_VIEWER_URL", DEFAULT_VIEWER_URL)


async def main(remote_path: str, wait_seconds: float) -> None:
    agent_id = os.environ.get("ETHANDESK_AGENT_ID", "")
    code = os.environ.get("ETHANDESK_SESSION_CODE", "")
    if not agent_id:
        print("RESULT failed 缺少 ETHANDESK_AGENT_ID", flush=True)
        raise SystemExit(2)

    started = time.time()
    client = DeskClient(URL, password=PASSWORD)
    session = None
    try:
        await client.login()
        session = await client.open_session(agent_id, code)
        print(f"session_open {time.time() - started:.1f}s", flush=True)

        async def request() -> None:
            await asyncio.sleep(1.0)
            print(f"list {remote_path!r}", flush=True)
            # 线上中继只转发已知类型，请求借 file_offer 承载（direction="list"）。
            await session.send_text({"t": "file_offer", "direction": "list", "path": remote_path})

        task = asyncio.create_task(request())
        listing: dict = {}
        while time.time() - started < wait_seconds:
            kind, payload = await session.receive()
            if kind == "closed":
                print("RESULT failed 会话已断开", flush=True)
                return
            if kind != "text" or not isinstance(payload, dict):
                continue
            if payload.get("t") == "log":
                print(f"agent_log {payload.get('text')}", flush=True)
                continue
            if payload.get("t") == "meta" and payload.get("kind") == "file_list":
                listing = payload
                break
        task.cancel()

        if not listing:
            print("RESULT failed 没收到目录内容", flush=True)
            return
        error = str(listing.get("error") or "")
        entries = list(listing.get("entries") or [])
        print(f"path {listing.get('path')!r} entries={len(entries)}", flush=True)
        for entry in entries[:12]:
            flag = "DIR " if entry.get("dir") else "FILE"
            print(f"  {flag} {entry.get('name')} {entry.get('size')}", flush=True)
        if error:
            print(f"RESULT rejected {error}", flush=True)
            return
        print("RESULT ok", flush=True)
    except Exception as exc:
        print(f"RESULT failed {exc!r}", flush=True)
    finally:
        if session is not None:
            await session.close()
        await client.close()


if __name__ == "__main__":
    path = sys.argv[1] if len(sys.argv) > 1 else ""
    limit = float(sys.argv[2]) if len(sys.argv) > 2 else 15.0
    asyncio.run(main(path, limit))
