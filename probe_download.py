"""真机验“远端取文件”：主控端发 file_get，被控端回 direction="down" 的整份文件。

跟客户端走同一条协议（登录 → 开会话 → file_get → 收 offer/分块 → 落盘比对 sha256），
所以通过就等于主控端「下载远程文件…」真能用。

用法：python probe_download.py <被控端文件绝对路径> [最多等待秒数，默认 25]
被控端：ETHANDESK_AGENT_ID / ETHANDESK_SESSION_CODE；口令 ETHANDESK_RELAY_PASSWORD。
保存位置：环境变量 ETHANDESK_DOWNLOAD_DIR，默认 %TEMP%\\ethandesk_downloads。
可选：ETHANDESK_EXPECT_SHA256 校验远端文件的哈希。
"""

from __future__ import annotations

import asyncio
import hashlib
import os
import sys
import time

from agent.files_transfer import IncomingFile
from common.defaults import DEFAULT_VIEWER_URL
from common.protocol import BinaryPairer
from viewer.client import DeskClient

PASSWORD = os.environ.get("ETHANDESK_RELAY_PASSWORD", "")
URL = os.environ.get("ETHANDESK_VIEWER_URL", DEFAULT_VIEWER_URL)


async def main(remote_path: str, wait_seconds: float) -> None:
    agent_id = os.environ.get("ETHANDESK_AGENT_ID", "")
    code = os.environ.get("ETHANDESK_SESSION_CODE", "")
    if not agent_id:
        print("RESULT failed 缺少 ETHANDESK_AGENT_ID", flush=True)
        raise SystemExit(2)
    save_dir = os.environ.get("ETHANDESK_DOWNLOAD_DIR") or os.path.join(
        os.environ.get("TEMP", "."), "ethandesk_downloads"
    )
    os.makedirs(save_dir, exist_ok=True)
    expect = os.environ.get("ETHANDESK_EXPECT_SHA256", "").lower()

    pairer = BinaryPairer()
    incoming_pkg = {"file": None}
    offer: dict = {}
    stopped: dict = {"path": None}

    started = time.time()
    client = DeskClient(URL, password=PASSWORD)
    session = None
    try:
        await client.login()
        session = await client.open_session(agent_id, code)
        print(f"session_open {time.time() - started:.1f}s", flush=True)
        await session.send_text({"t": "set_quality", "scale": 0.5, "quality": 45, "fps": 5})

        async def request() -> None:
            await asyncio.sleep(1.0)
            print(f"file_get {remote_path}", flush=True)
            await session.send_text({"t": "file_get", "path": remote_path})

        task = asyncio.create_task(request())
        while time.time() - started < wait_seconds:
            kind, payload = await session.receive()
            if kind == "closed":
                print("RESULT failed 会话已断开", flush=True)
                return
            if kind == "text":
                if not isinstance(payload, dict):
                    continue
                if payload.get("t") == "bin":
                    pairer.header(payload)
                    continue
                if payload.get("t") == "file_offer":
                    offer = payload
                    if payload.get("direction") != "down":
                        continue
                    incoming_pkg["file"] = IncomingFile(
                        str(payload.get("id")),
                        str(payload.get("name", "file.bin")),
                        int(payload.get("size", 0)),
                        save_dir,
                        payload.get("sha256"),
                    )
                    print(
                        f"offer {payload.get('name')} {payload.get('size')}B "
                        f"direction={payload.get('direction')}",
                        flush=True,
                    )
                    continue
                if payload.get("t") == "log":
                    print(f"agent_log {payload.get('text')}", flush=True)
                    continue
                continue
            if kind == "bin":
                try:
                    header, chunk = pairer.attach(payload)
                except Exception:
                    continue
                if header.get("kind") != "file":
                    continue
                incoming = incoming_pkg["file"]
                if incoming is None:
                    continue
                incoming.feed(int(header.get("seq", 0)), chunk)
                if incoming.received >= incoming.expected_size:
                    stopped["path"] = incoming.finish()
                    break
        task.cancel()
        path = stopped["path"]
        if path is None:
            print("RESULT failed 没收到完整文件", flush=True)
            return
        with open(path, "rb") as handle:
            got = hashlib.sha256(handle.read()).hexdigest()
        print(f"saved {path} sha256={got}", flush=True)
        if offer.get("sha256") and got != offer.get("sha256"):
            print("RESULT failed 与 offer 声明的 sha256 不一致", flush=True)
            return
        if expect and got != expect:
            print(f"RESULT failed 与期望 sha256 不一致（期望 {expect}）", flush=True)
            return
        print("RESULT ok", flush=True)
    except Exception as exc:
        print(f"RESULT failed {exc!r}", flush=True)
    finally:
        if session is not None:
            await session.close()
        await client.close()


if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("用法：python probe_download.py <被控端文件绝对路径> [等待秒数]")
        raise SystemExit(2)
    limit = float(sys.argv[2]) if len(sys.argv) > 2 else 25.0
    asyncio.run(main(sys.argv[1], limit))
