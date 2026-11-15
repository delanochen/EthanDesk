"""真机验「双栏上传」：主控端把文件推到被控端指定目录，列回确认，再取回比对。

走的是「远程文件」窗口里「上传 →」那条协议：
  file_offer(direction=up, dir=远端绝对目录) → 被控端落到该目录 → 回 file_done
目录无效时被控端回 log「上传被拒绝：目标目录无效：…」，不落盘。

三件事一起验：
  1) 上传落到指定目录（列目录能看到同名同尺寸的文件）
  2) 目标目录无效时被明确拒绝，且没有写盘
  3) 再取回该文件，sha256 与本机源文件一致

用法：python probe_upload.py [远端目标目录] [每个场景最多等待秒数，默认 30]
被控端：ETHANDESK_AGENT_ID / ETHANDESK_SESSION_CODE；口令 ETHANDESK_RELAY_PASSWORD。
"""

from __future__ import annotations

import asyncio
import hashlib
import os
import sys
import tempfile
import time

from agent.files_transfer import FileTransferError, IncomingFile, OutgoingFile
from common.defaults import DEFAULT_VIEWER_URL
from common.protocol import BinaryPairer, ProtocolError
from viewer.client import DeskClient

PASSWORD = os.environ.get("ETHANDESK_RELAY_PASSWORD", "")
URL = os.environ.get("ETHANDESK_VIEWER_URL", DEFAULT_VIEWER_URL)
SIZE = 1024 * 1024


def sha256_of(path: str) -> str:
    digest = hashlib.sha256()
    with open(path, "rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def remote_join(directory: str, name: str) -> str:
    """被控端是 Windows，本地是 Linux，别让 os.path.join 把分隔符混起来。"""
    sep = "\\" if "\\" in directory else "/"
    return directory.rstrip("\\/") + sep + name


class Probe:
    def __init__(self, session, download_dir: str) -> None:
        self.session = session
        self.download_dir = download_dir
        self.pairer = BinaryPairer()
        self.incoming: dict[str, IncomingFile] = {}
        self.logs: list[str] = []
        self.dones: list[str] = []
        self.lists: dict[str, dict] = {}
        self.saved: list[str] = []

    async def pump(self, until, seconds: float) -> None:
        """读到直到 ``until()`` 为真、或超时、或会话断开。"""
        deadline = time.time() + seconds
        while time.time() < deadline:
            if until():
                return
            remaining = deadline - time.time()
            if remaining <= 0:
                return
            try:
                kind, payload = await asyncio.wait_for(self.session.receive(), remaining)
            except asyncio.TimeoutError:
                return
            if kind == "closed":
                print("agent 会话已断开", flush=True)
                return
            if kind == "bin":
                self._binary(payload)
                continue
            if kind != "text" or not isinstance(payload, dict):
                continue
            self._text(payload)

    def _text(self, message: dict) -> None:
        kind = message.get("t")
        if kind == "bin":
            self.pairer.header(message)
            return
        if kind == "log":
            text = str(message.get("text", ""))
            self.logs.append(text)
            print(f"agent_log {text}", flush=True)
            return
        if kind == "file_done":
            self.dones.append(str(message.get("id", "")))
            return
        if kind == "meta" and message.get("kind") == "file_list":
            self.lists[str(message.get("path", ""))] = message
            return
        if kind == "file_offer":
            self._offer(message)

    def _offer(self, message: dict) -> None:
        if message.get("direction") != "down":
            return
        transfer_id = str(message.get("id", ""))
        try:
            self.incoming[transfer_id] = IncomingFile(
                transfer_id,
                str(message.get("name", "file.bin")),
                int(message.get("size", 0)),
                self.download_dir,
                message.get("sha256"),
            )
        except (FileTransferError, ValueError) as exc:
            print(f"回程 offer 无效：{exc}", flush=True)

    def _binary(self, data: bytes) -> None:
        try:
            header, payload = self.pairer.attach(data)
        except ProtocolError:
            return
        if header.get("kind") != "file":
            return
        transfer_id = str(header.get("id", ""))
        incoming = self.incoming.get(transfer_id)
        if incoming is None:
            return
        try:
            incoming.feed(int(header.get("seq", 0)), payload)
            if incoming.received >= incoming.expected_size:
                path = incoming.finish()
                self.incoming.pop(transfer_id, None)
                self.saved.append(path)
                print(f"saved {path} {os.path.getsize(path)}B", flush=True)
        except (FileTransferError, OSError) as exc:
            print(f"回程写盘失败：{exc}", flush=True)


async def upload(probe: Probe, path: str, remote_dir: str) -> str:
    with open(path, "rb") as handle:
        data = handle.read()
    name = os.path.basename(path)
    outgoing = OutgoingFile(os.urandom(6).hex(), name, data, direction="up")
    offer = outgoing.offer()
    offer["dir"] = remote_dir
    await probe.session.send_text(offer)
    for header, chunk in outgoing.frames():
        await probe.session.send_bin(header, chunk)
    print(f"upload offer {offer['id']} {name} {len(data)}B → {remote_dir}", flush=True)
    return offer["id"]


async def main(target_dir: str, wait_seconds: float) -> None:
    agent_id = os.environ.get("ETHANDESK_AGENT_ID", "")
    code = os.environ.get("ETHANDESK_SESSION_CODE", "")
    if not agent_id:
        print("RESULT failed 缺少 ETHANDESK_AGENT_ID", flush=True)
        raise SystemExit(2)

    local_dir = os.path.join(tempfile.gettempdir(), "probe_upload_local")
    os.makedirs(local_dir, exist_ok=True)
    name = f"probe_upload_{os.urandom(4).hex()}.bin"
    source = os.path.join(local_dir, name)
    payload = os.urandom(SIZE)
    with open(source, "wb") as handle:
        handle.write(payload)
    source_sha = sha256_of(source)
    print(f"source {source} {os.path.getsize(source)}B sha={source_sha[:12]}", flush=True)

    started = time.time()
    client = DeskClient(URL, password=PASSWORD)
    session = None
    failures: list[str] = []
    try:
        await client.login()
        session = await client.open_session(agent_id, code)
        print(f"session_open {time.time() - started:.1f}s", flush=True)

        remote_dir = os.path.join(tempfile.gettempdir(), "probe_upload_sink")
        os.makedirs(remote_dir, exist_ok=True)
        probe = Probe(session, remote_dir)

        # 1) 上传到指定目录
        upload_id = await upload(probe, source, target_dir)
        await probe.pump(lambda: upload_id in probe.dones, wait_seconds)
        if upload_id not in probe.dones:
            failures.append("上传没收到 file_done")
        else:
            print("file_done 已收到", flush=True)

        # 2) 列回该目录，确认同名同尺寸
        probe.lists.clear()
        await session.send_text({"t": "file_offer", "direction": "list", "path": target_dir})
        await probe.pump(lambda: target_dir in probe.lists, wait_seconds)
        listing = probe.lists.get(target_dir)
        if not listing:
            failures.append("列目录没有回内容")
        elif listing.get("error"):
            failures.append(f"列目录报错：{listing.get('error')}")
        else:
            entries = list(listing.get("entries") or [])
            hit = [item for item in entries if item.get("name") == name]
            print(f"listing {target_dir} entries={len(entries)} hit={hit}", flush=True)
            if not hit:
                failures.append("远端目录里没出现刚上传的文件")
            elif int(hit[0].get("size", -1)) != SIZE:
                failures.append(f"远端尺寸不符：{hit[0].get('size')} != {SIZE}")

        # 3) 目标目录无效 → 必须被拒绝，且不能落盘
        bad_dir = remote_join(target_dir, f"no-such-dir-{os.urandom(2).hex()}")
        probe.dones.clear()
        probe.logs.clear()
        bad_id = await upload(probe, source, bad_dir)
        await probe.pump(lambda: any("上传被拒绝" in line for line in probe.logs), wait_seconds)
        rejected = next((line for line in probe.logs if "上传被拒绝" in line), "")
        if not rejected:
            failures.append("无效目录没有被拒绝")
        else:
            print(f"rejected {rejected}", flush=True)
        if bad_id in probe.dones:
            failures.append("无效目录居然回了 file_done")

        # 4) 取回并比对 sha256
        probe.lists.clear()
        remote_file = remote_join(target_dir, name)
        await session.send_text({"t": "file_offer", "direction": "get", "path": remote_file})
        await probe.pump(lambda: bool(probe.saved), wait_seconds)
        if not probe.saved:
            failures.append("取回没有落盘")
        else:
            back_sha = sha256_of(probe.saved[-1])
            print(f"back sha={back_sha[:12]}", flush=True)
            if back_sha != source_sha:
                failures.append("取回的 sha256 与源文件不一致")

        print(f"remote_cleanup 请手工删除 {remote_file}", flush=True)
        if failures:
            print("RESULT failed " + "；".join(failures), flush=True)
        else:
            print("RESULT ok", flush=True)
    except Exception as exc:
        print(f"RESULT failed {exc!r}", flush=True)
    finally:
        if session is not None:
            await session.close()
        await client.close()


if __name__ == "__main__":
    remote = sys.argv[1] if len(sys.argv) > 1 else ""
    limit = float(sys.argv[2]) if len(sys.argv) > 2 else 30.0
    if not remote:
        print("RESULT failed 需要远端目标目录", flush=True)
        raise SystemExit(2)
    asyncio.run(main(remote, limit))
