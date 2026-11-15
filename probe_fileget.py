"""验「原生 file_get 走中继」：老中继的 KNOWN_TYPES 里没有它，消息会被整条丢掉，
所以主控端一直借 file_offer(direction=get) 绕路。中继升级后这条直路应该通了。

跑法：python probe_fileget.py <被控端文件绝对路径> [秒数，默认 20]
被控端：ETHANDESK_AGENT_ID / ETHANDESK_SESSION_CODE；口令 ETHANDESK_RELAY_PASSWORD；
ETHANDESK_VIEWER_URL 可指向本地转发端口。
"""

from __future__ import annotations

import asyncio
import hashlib
import os
import sys
import tempfile
import time

from agent.files_transfer import FileTransferError, IncomingFile
from common.defaults import DEFAULT_VIEWER_URL
from common.protocol import BinaryPairer, ProtocolError
from viewer.client import DeskClient

PASSWORD = os.environ.get("ETHANDESK_RELAY_PASSWORD", "")
URL = os.environ.get("ETHANDESK_VIEWER_URL", DEFAULT_VIEWER_URL)


async def main(remote_file: str, wait_seconds: float) -> None:
    agent_id = os.environ.get("ETHANDESK_AGENT_ID", "")
    code = os.environ.get("ETHANDESK_SESSION_CODE", "")
    if not agent_id:
        print("RESULT failed 缺少 ETHANDESK_AGENT_ID", flush=True)
        raise SystemExit(2)

    sink = os.path.join(tempfile.gettempdir(), "probe_fileget_sink")
    os.makedirs(sink, exist_ok=True)
    pairer = BinaryPairer()
    incoming: dict[str, IncomingFile] = {}
    offers: list[dict] = []
    saved: list[str] = []

    started = time.time()
    client = DeskClient(URL, password=PASSWORD)
    session = None
    try:
        await client.login()
        session = await client.open_session(agent_id, code)
        print(f"session_open {time.time() - started:.1f}s", flush=True)
        await session.send_text({"t": "file_get", "path": remote_file})
        print(f"file_get {remote_file!r}", flush=True)

        deadline = started + wait_seconds
        while time.time() < deadline and not saved:
            remaining = deadline - time.time()
            if remaining <= 0:
                break
            try:
                kind, payload = await asyncio.wait_for(session.receive(), remaining)
            except asyncio.TimeoutError:
                break
            if kind == "closed":
                print("会话已断开", flush=True)
                break
            if kind == "bin" and isinstance(payload, bytes):
                try:
                    header, body = pairer.attach(payload)
                except ProtocolError:
                    continue
                if header.get("kind") != "file":
                    continue
                item = incoming.get(str(header.get("id", "")))
                if item is None:
                    continue
                try:
                    item.feed(int(header.get("seq", 0)), body)
                    if item.received >= item.expected_size:
                        path = item.finish()
                        saved.append(path)
                        print(f"saved {path} {os.path.getsize(path)}B", flush=True)
                except (FileTransferError, OSError) as exc:
                    print(f"写盘失败：{exc}", flush=True)
                continue
            if kind != "text" or not isinstance(payload, dict):
                continue
            if payload.get("t") == "bin":
                # 分块是先一条 bin 头、再一条二进制；不把头交给 pairer，后面的
                # 数据块就没有配对的头，会被整块丢掉。
                try:
                    pairer.header(payload)
                except ProtocolError:
                    pass
                continue
            if payload.get("t") == "log":
                print(f"agent_log {payload.get('text')}", flush=True)
                continue
            if payload.get("t") == "file_offer" and payload.get("direction") == "down":
                offers.append(payload)
                transfer_id = str(payload.get("id", ""))
                try:
                    incoming[transfer_id] = IncomingFile(
                        transfer_id,
                        str(payload.get("name", "file.bin")),
                        int(payload.get("size", 0)),
                        sink,
                        payload.get("sha256"),
                    )
                except (FileTransferError, ValueError) as exc:
                    print(f"offer 无效：{exc}", flush=True)

        if not offers:
            print("RESULT failed 没收到 down offer（中继可能仍把 file_get 丢掉）", flush=True)
            return
        if not saved:
            print(f"RESULT failed 收到 offer 但没落盘：{offers[0]}", flush=True)
            return
        digest = hashlib.sha256(open(saved[-1], "rb").read()).hexdigest()
        print(f"sha256 {digest[:16]}", flush=True)
        print("RESULT ok", flush=True)
    except Exception as exc:
        print(f"RESULT failed {exc!r}", flush=True)
    finally:
        if session is not None:
            await session.close()
        await client.close()


if __name__ == "__main__":
    target = sys.argv[1] if len(sys.argv) > 1 else ""
    limit = float(sys.argv[2]) if len(sys.argv) > 2 else 20.0
    if not target:
        print("RESULT failed 需要被控端文件路径", flush=True)
        raise SystemExit(2)
    asyncio.run(main(target, limit))
