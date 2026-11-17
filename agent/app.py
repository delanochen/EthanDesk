"""Windows agent: a small window plus an asyncio worker that serves the relay.

Design rules that keep this honest:
* every session needs a local "允许远程控制" confirmation, there is no unattended mode;
* the worker owns all network state, the Tk thread only renders events and sends commands;
* frames are only produced when the viewer acknowledged the previous one, so a slow
  viewer cannot make the agent queue gigabytes of JPEGs.
"""

from __future__ import annotations

import asyncio
import json
import os
import queue
import threading
import time
from typing import Any

import aiohttp

from agent import clip, inject, screen, winlogin
from agent.config import AgentConfig
from agent.files_transfer import FileTransferError, IncomingFile, OutgoingFile
from common import session_code
from common.listing import list_directory as scan_directory
from common.protocol import BinaryPairer, ProtocolError, decode, encode, make_bin_header
from common.session_code import POLICY_DAILY, POLICY_EVERY_SESSION, POLICY_NEVER

POLICY_LABELS = {
    POLICY_EVERY_SESSION: "每次被控后",
    POLICY_DAILY: "每日",
    POLICY_NEVER: "永不",
}
POLICY_BY_LABEL = {label: policy for policy, label in POLICY_LABELS.items()}

CONSENT_TIMEOUT = 30.0
CLIPBOARD_POLL = 0.6
PING_INTERVAL = 20.0
RECONNECT_DELAY = 3.0
# 允许同时在途的未确认帧数，以及应答丢失后的自愈阈值（秒）
MAX_INFLIGHT = 3
ACK_TIMEOUT = 2.0
# 链路停滞阈值：这么久没收到任何消息就主动断开重连。Cloudflare 那条 WebSocket
# 路径会偶发几十秒不吐数据，aiohttp 不会自己判定超时，agent 会卡在半开连接上，
# 表现为“进程还在但中继显示离线”。
STALL_TIMEOUT = 60.0
# 单次发送/关闭的最长等待，避免发送被卡住时心跳线程一起挂死。
SEND_TIMEOUT = 15.0
# 首帧前等主控端下发画面档位的时间。主控端建立会话时就立刻下发（实测 0.3s 内），
# 这里只是给不配合的客户端兜底；3 秒太长，慢链路上用户会以为“连上了但没画面”。
SETTINGS_GRACE = 1.0
# 主控端能远程取走的单个文件上限。整份读进内存再分块发，超过这个尺寸先把请求拒掉，
# 免得被一个大文件把被控端进程撑爆。
MAX_PULL_BYTES = 512 * 1024 * 1024
# 一次目录列举最多回多少条：远程文件浏览器只需要看一屏，巨目录全列会把消息撑大，
# 主控端也没法读。
MAX_LIST_ENTRIES = 500


def link_stalled(last_rx: float, now: float) -> bool:
    """True when nothing has arrived for ``STALL_TIMEOUT`` seconds."""
    return last_rx > 0 and (now - last_rx) > STALL_TIMEOUT


class AgentWorker:
    """Owns the relay connection, capture loop, clipboard sync and file transfers."""

    def __init__(self, config: AgentConfig, events: "queue.Queue", commands: "queue.Queue") -> None:
        self.config = config
        self.events = events
        self.commands = commands
        self._loop: asyncio.AbstractEventLoop | None = None
        self._thread: threading.Thread | None = None
        self._stop = threading.Event()
        self._capturer: screen.ScreenCapturer | None = None
        self._pairer = BinaryPairer()
        self._allow_input = config.allow_input
        self._clipboard_sync = config.clipboard_sync
        self._quality = config.quality
        self._scale = config.scale
        self._fps = config.fps
        self._inflight = 0
        self._inflight_since = 0.0
        self._active_session: str | None = None
        self._capture_task: asyncio.Task | None = None
        self._consent: dict[str, asyncio.Future] = {}
        self._incoming: dict[str, IncomingFile] = {}
        self._last_clip_signature = ""
        # Diagnostics for the control path: 鼠标/键盘到底有没有到、换算成了哪个像素。
        # 关着的时候一行都不打，开的时候每类事件最多 1 秒一条。
        self._debug_input = bool(os.environ.get("ETHANDESK_DEBUG_INPUT"))
        self._last_input_log = 0.0
        self._last_rx = 0.0
        self._settings_event: asyncio.Event | None = None
        # 上一次主控端实际用过的档位。跨洋链路上 set_quality 可能比会话启动还慢，
        # 首帧若按本机配置的 1.0×/q70 编码就又把链路堵死；下次会话直接沿用它。
        self._remote_profile: "tuple[float, int, int] | None" = None
        self._last_capture_error = ""
        self._first_frame_logged = False
        self._connected = False
        self._ws: aiohttp.ClientWebSocketResponse | None = None

    # ---------------------------------------------------------------- lifecycle
    def start(self) -> None:
        if self._thread and self._thread.is_alive():
            return
        self._stop.clear()
        self._thread = threading.Thread(target=self._run, name="deskrelay-agent", daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        loop = self._loop
        if loop and not loop.is_closed():
            loop.call_soon_threadsafe(self._cancel_all)

    def _cancel_all(self) -> None:
        if self._capture_task and not self._capture_task.done():
            self._capture_task.cancel()
        for future in self._consent.values():
            if not future.done():
                future.cancel()

    def _run(self) -> None:
        asyncio.run(self._supervise())

    async def _supervise(self) -> None:
        self._loop = asyncio.get_running_loop()
        self._capturer = screen.ScreenCapturer()
        while not self._stop.is_set():
            try:
                await self._connect_once()
            except aiohttp.ClientError as exc:
                self._emit("log", f"连接失败：{exc}")
            except asyncio.CancelledError:
                break
            except Exception as exc:  # keep the agent alive across unexpected faults
                self._emit("log", f"连接异常：{exc!r}")
            await asyncio.sleep(RECONNECT_DELAY)
        if self._capturer is not None:
            self._capturer.close()

    # ---------------------------------------------------------------- thread bridge
    def _emit(self, kind: str, payload: Any = None) -> None:
        self.events.put((kind, payload))

    def answer_consent(self, session_id: str, accepted: bool) -> None:
        loop = self._loop
        if loop is None or loop.is_closed():
            return
        loop.call_soon_threadsafe(self._resolve_consent, session_id, accepted)

    def _resolve_consent(self, session_id: str, accepted: bool) -> None:
        future = self._consent.pop(session_id, None)
        if future and not future.done():
            future.set_result(accepted)

    def apply_settings(self, **kwargs: Any) -> None:
        if "allow_input" in kwargs:
            self._allow_input = bool(kwargs["allow_input"])
        if "clipboard_sync" in kwargs:
            self._clipboard_sync = bool(kwargs["clipboard_sync"])
        if "require_login" in kwargs:
            self.config.require_login = bool(kwargs["require_login"])
        if "login_accounts" in kwargs:
            self.config.login_accounts = [str(item) for item in (kwargs["login_accounts"] or [])]
        if "quality" in kwargs:
            self._quality = int(kwargs["quality"])
        if "scale" in kwargs:
            self._scale = float(kwargs["scale"])
        if "fps" in kwargs:
            self._fps = int(kwargs["fps"])

    # ---------------------------------------------------------------- connection
    async def _connect_once(self) -> None:
        self._emit("log", f"正在连接 {self.config.relay_url}")
        async with aiohttp.ClientSession() as session:
            async with session.ws_connect(
                self.config.relay_url, heartbeat=30, max_msg_size=0, autoping=True
            ) as ws:
                self._connected = True
                self._ws = ws
                await ws.send_str(
                    encode(
                        {
                            "t": "hello",
                            "agent_id": self.config.agent_id,
                            "key": self.config.agent_key,
                            "info": self.config.info(),
                        }
                    )
                )
                self._emit("status", "connecting")
                self._last_rx = time.monotonic()
                heartbeat = asyncio.create_task(self._heartbeat(ws))
                clip_task = asyncio.create_task(self._clipboard_loop(ws))
                try:
                    async for message in ws:
                        await self._handle_message(ws, message)
                        if self._stop.is_set():
                            break
                finally:
                    heartbeat.cancel()
                    clip_task.cancel()
                    self._connected = False
                    self._ws = None
                    self._stop_capture()
                    self._emit("status", "offline")
                    self._emit("log", "连接已断开")

    async def _heartbeat(self, ws: aiohttp.ClientWebSocketResponse) -> None:
        """Ping the relay, and force a reconnect when the link goes quiet.

        A stalled Cloudflare path leaves ``async for message in ws`` waiting forever,
        so the watchdog here is what actually ends a dead connection.
        """
        try:
            while not ws.closed:
                await asyncio.sleep(PING_INTERVAL)
                if link_stalled(self._last_rx, time.monotonic()):
                    self._emit("log", f"链路 {STALL_TIMEOUT:.0f}s 无响应，主动重连")
                    break
                try:
                    await asyncio.wait_for(
                        ws.send_str(encode({"t": "ping", "ts": time.time()})), SEND_TIMEOUT
                    )
                except (asyncio.TimeoutError, ConnectionResetError, RuntimeError):
                    self._emit("log", "心跳发送超时，主动重连")
                    break
        except (ConnectionResetError, asyncio.CancelledError):
            pass
        finally:
            if not ws.closed:
                try:
                    await asyncio.wait_for(ws.close(), SEND_TIMEOUT)
                except (asyncio.TimeoutError, ConnectionResetError, RuntimeError):
                    pass

    async def _clipboard_loop(self, ws: aiohttp.ClientWebSocketResponse) -> None:
        try:
            while not ws.closed:
                await asyncio.sleep(CLIPBOARD_POLL)
                if not self._clipboard_sync:
                    continue
                try:
                    text = await asyncio.to_thread(clip.get_text)
                except clip.ClipboardUnavailable:
                    continue
                signature = clip.text_signature(text)
                if signature == self._last_clip_signature:
                    continue
                self._last_clip_signature = signature
                if text:
                    await ws.send_str(encode({"t": "clip", "dir": "to_viewer", "text": text}))
        except (ConnectionResetError, asyncio.CancelledError):
            pass

    # ---------------------------------------------------------------- messaging
    async def _handle_message(
        self, ws: aiohttp.ClientWebSocketResponse, message: aiohttp.WSMessage
    ) -> None:
        self._last_rx = time.monotonic()
        if message.type == aiohttp.WSMsgType.TEXT:
            try:
                decoded = decode(message.data)
            except ProtocolError:
                return
            if decoded is None:
                return
            await self._handle_text(ws, decoded)
            return
        if message.type == aiohttp.WSMsgType.BINARY:
            await self._handle_binary(ws, message.data)
            return
        if message.type in (aiohttp.WSMsgType.CLOSED, aiohttp.WSMsgType.ERROR):
            self._stop.set()

    async def _handle_text(self, ws: aiohttp.ClientWebSocketResponse, message: dict) -> None:
        kind = message.get("t")

        if kind == "hello_ok":
            self._emit("status", "online")
            self._emit("log", f"已注册，设备 ID {self.config.agent_id}")
            return
        if kind == "error":
            self._emit("log", f"中继错误：{message.get('code')}")
            return
        if kind == "ping":
            await ws.send_str(encode({"t": "pong"}))
            return
        if kind == "session_request":
            await self._ask_consent(ws, message)
            return
        if kind == "session_start":
            await self._on_session_start(ws)
            return
        if kind == "session_close":
            self._stop_capture()
            if self.config.session_code_policy == POLICY_EVERY_SESSION:
                self.config.rotate_session_code()
                self._save_config()
                self._emit("log", f"验证码已更新：{self.config.session_code}")
            self._emit("log", "远控会话已结束")
            return
        if kind == "input":
            if self._allow_input and self._active_session:
                try:
                    applied = inject.apply_input(
                        message, self._capturer.bounds if self._capturer else None
                    )
                    self._note_input(message, applied)
                except inject.InputInjectionError as exc:
                    self._emit("log", f"输入注入失败：{exc}")
                except Exception as exc:  # 单条输入不能把整条远控会话带走
                    self._emit("log", f"输入事件处理异常（{message.get('ev')}）：{exc}")
            elif self._debug_input:
                self._emit(
                    "log",
                    f"输入被忽略（allow_input={self._allow_input} "
                    f"session={bool(self._active_session)}）：{message.get('ev')}",
                )
            return
        if kind == "set_quality":
            self.apply_settings(
                quality=message.get("quality", self._quality),
                scale=message.get("scale", self._scale),
                fps=message.get("fps", self._fps),
            )
            self._remote_profile = (self._scale, self._quality, self._fps)
            if self._settings_event is not None:
                self._settings_event.set()
            return
        if kind == "frame_recv":
            self._inflight = max(0, self._inflight - 1)
            self._inflight_since = 0.0
            return
        if kind == "clip":
            if self._clipboard_sync and message.get("dir") == "to_agent":
                text = str(message.get("text", ""))
                try:
                    await asyncio.to_thread(clip.set_text, text)
                    self._last_clip_signature = clip.text_signature(text)
                except clip.ClipboardUnavailable as exc:
                    self._emit("log", f"写入剪贴板失败：{exc}")
            return
        if kind == "file_offer":
            # 线上中继只转发它认识的类型，所以「远端取文件」借用 file_offer 承载，
            # 用 direction="get" 表达请求、direction="list" 表达列目录；
            # file_get 留给升级后的中继直连。
            direction = message.get("direction")
            if direction == "get":
                await self._serve_get(ws, message)
            elif direction == "list":
                await self._serve_list(ws, message)
            else:
                refusal = self._open_incoming(message)
                if refusal:
                    await self._notify_viewer(ws, refusal)
            return
        if kind == "file_get":
            await self._serve_get(ws, message)
            return
        if kind == "bin":
            self._pairer.header(message)
            return

    def _note_input(self, message: dict, applied: bool) -> None:
        if not self._debug_input:
            return
        now = time.perf_counter()
        if now - self._last_input_log < 1.0:
            return
        self._last_input_log = now
        event = message.get("ev")
        detail = f"x={message.get('x')} y={message.get('y')}"
        if event == "text":
            detail = f"text={str(message.get('text'))[:24]!r}"
        elif event in ("key_down", "key_up"):
            detail = f"keysym={message.get('keysym')}"
        elif event in ("mouse_down", "mouse_up"):
            detail = f"button={message.get('button')}"
        self._emit("log", f"输入 {event} {detail} applied={applied}")

    async def _handle_binary(self, ws: aiohttp.ClientWebSocketResponse, data: bytes) -> None:
        try:
            header, payload = self._pairer.attach(data)
        except ProtocolError:
            return
        if header.get("kind") != "file":
            return
        transfer_id = str(header.get("id", ""))
        incoming = self._incoming.get(transfer_id)
        if incoming is None:
            return
        try:
            incoming.feed(int(header.get("seq", 0)), payload)
        except FileTransferError as exc:
            self._emit("log", f"文件分块错误：{exc}")
            self._incoming.pop(transfer_id, None)
            return
        if incoming.received >= incoming.expected_size:
            try:
                path = incoming.finish()
                self._emit("log", f"已接收文件：{path}")
                await ws.send_str(encode({"t": "file_done", "id": transfer_id}))
            except (FileTransferError, OSError) as exc:
                self._emit("log", f"保存文件失败：{exc}")
            finally:
                self._incoming.pop(transfer_id, None)

    def _verify_login(self, user: str, password: str) -> tuple[bool, str]:
        """账户门禁。单独成方法，测试里可以替换掉，不必真的调 Win32 登录。"""
        return winlogin.verify(user, password, self.config.login_accounts)

    async def _ask_consent(self, ws: aiohttp.ClientWebSocketResponse, message: dict) -> None:
        session_id = str(message.get("sid", ""))
        viewer = str(message.get("viewer", "viewer"))
        if self._active_session:
            await ws.send_str(encode({"t": "session_denied", "sid": session_id, "code": "agent_busy"}))
            return

        # 账户门禁优先于验证码和人工确认：开了就只认 Windows 账户密码。
        if self.config.require_login:
            user = str(message.get("user", "") or "")
            ok, reason = self._verify_login(user, str(message.get("password", "") or ""))
            if not ok:
                hint = (
                    "该账户不在允许名单" if reason == winlogin.ACCOUNT_NOT_ALLOWED else "账户或密码不正确"
                )
                await ws.send_str(
                    encode(
                        {
                            "t": "session_denied",
                            "sid": session_id,
                            "code": reason,
                            "need_login": True,
                        }
                    )
                )
                self._emit("log", f"拒绝 {viewer}：{hint}")
                return
            self._active_session = session_id
            await ws.send_str(encode({"t": "session_accept", "sid": session_id}))
            self._emit("log", f"{viewer} 通过账户验证接入（{winlogin.normalise_account(user)}）")
            return

        if self.config.session_code_policy == POLICY_DAILY and session_code.should_rotate(
            POLICY_DAILY, self.config.code_rotated_at
        ):
            self.config.rotate_session_code()
            self._save_config()
            self._emit("log", f"验证码已更新：{self.config.session_code}")

        code = str(message.get("code", ""))
        if self.config.unattended and session_code.matches(self.config.session_code, code):
            self._active_session = session_id
            await ws.send_str(encode({"t": "session_accept", "sid": session_id}))
            self._emit("log", f"{viewer} 凭验证码免确认接入（无人值守）")
            return

        future: asyncio.Future = asyncio.get_running_loop().create_future()
        self._consent[session_id] = future
        self._emit("consent_request", {"sid": session_id, "viewer": viewer})
        try:
            accepted = await asyncio.wait_for(future, CONSENT_TIMEOUT)
        except asyncio.TimeoutError:
            accepted = False
        finally:
            self._consent.pop(session_id, None)

        if not accepted:
            await ws.send_str(encode({"t": "session_denied", "sid": session_id}))
            self._emit("log", "已拒绝远程会话")
            return

        self._active_session = session_id
        await ws.send_str(encode({"t": "session_accept", "sid": session_id}))
        self._emit("log", f"{viewer} 已接入，等待中继放行")

    def _save_config(self) -> None:
        try:
            self.config.save()
        except OSError as exc:
            self._emit("log", f"保存配置失败：{exc}")

    async def _on_session_start(self, ws: aiohttp.ClientWebSocketResponse) -> None:
        self._settings_event = asyncio.Event()
        # 每个会话都记一次首帧，便于按会话排查“连上了但没画面”。
        self._first_frame_logged = False
        if self._remote_profile is not None:
            self._scale, self._quality, self._fps = self._remote_profile
        if self._capturer is not None:
            await ws.send_str(
                encode(
                    {
                        "t": "meta",
                        **self._capturer.bounds,
                        "scale": self._scale,
                        "fps": self._fps,
                    }
                )
            )
        self._start_capture(ws)

    # ---------------------------------------------------------------- capture
    def _start_capture(self, ws: aiohttp.ClientWebSocketResponse) -> None:
        if self._capture_task and not self._capture_task.done():
            return
        self._inflight = 0
        self._inflight_since = 0.0
        self._capture_task = asyncio.create_task(self._capture_loop(ws))

    def _stop_capture(self) -> None:
        if self._capture_task and not self._capture_task.done():
            self._capture_task.cancel()
        self._capture_task = None
        self._active_session = None
        self._inflight = 0
        self._inflight_since = 0.0

    async def _capture_loop(self, ws: aiohttp.ClientWebSocketResponse) -> None:
        capturer = self._capturer
        if capturer is None:
            self._emit("log", "抓屏不可用：capturer 未初始化")
            return
        sequence = 0
        settings_event = self._settings_event
        if settings_event is not None and not settings_event.is_set():
            try:
                await asyncio.wait_for(settings_event.wait(), SETTINGS_GRACE)
            except asyncio.TimeoutError:
                pass
        try:
            while not ws.closed and self._active_session:
                if self._inflight >= MAX_INFLIGHT:
                    if self._inflight_since and time.perf_counter() - self._inflight_since > ACK_TIMEOUT:
                        # 应答在慢链路上丢了也别把帧流冻死
                        self._inflight = 0
                        self._inflight_since = 0.0
                    else:
                        await asyncio.sleep(0.004)
                        continue
                interval = 1.0 / max(1, self._fps)
                started = time.perf_counter()
                try:
                    payload, width, height = capturer.capture(self._scale, self._quality)
                except Exception as exc:  # mss raises ScreenShotError, not OSError
                    message = f"抓屏失败：{exc!r}"
                    if message != self._last_capture_error:
                        self._last_capture_error = message
                        self._emit("log", message)
                    await asyncio.sleep(0.5)
                    continue
                # 兜底抓屏生效/恢复都给主控端留一条记录，别让画面来源悄悄变了。
                note = str(getattr(capturer, "last_note", ""))
                if note != self._last_capture_error:
                    self._last_capture_error = note
                    if note:
                        self._emit("log", note)
                header = make_bin_header(
                    "frame", seq=sequence, size=len(payload), width=width, height=height
                )
                await ws.send_str(encode(header))
                await ws.send_bytes(payload)
                self._inflight += 1
                if self._inflight_since == 0.0:
                    self._inflight_since = time.perf_counter()
                sequence += 1
                if not self._first_frame_logged:
                    self._first_frame_logged = True
                    self._emit("log", f"首帧已发出 {width}x{height} {len(payload)}B")
                elapsed = time.perf_counter() - started
                await asyncio.sleep(max(0.0, interval - elapsed))
        except asyncio.CancelledError:
            pass
        except ConnectionResetError:
            pass

    # ---------------------------------------------------------------- commands
    def send_file(self, path: str) -> None:
        loop = self._loop
        if loop is None or loop.is_closed():
            self._emit("log", "未连接，无法发送文件")
            return
        asyncio.run_coroutine_threadsafe(self._send_file(path), loop)

    async def _send_file(self, path: str) -> None:
        ws = getattr(self, "_ws", None)
        if ws is None or ws.closed:
            self._emit("log", "会话未激活，无法发送文件")
            return
        await self._push_file(ws, path)

    async def _push_file(
        self, ws: aiohttp.ClientWebSocketResponse, path: str, *, reason: str = ""
    ) -> None:
        """Read one file and stream it to the viewer as a ``direction="down"`` offer."""
        try:
            with open(path, "rb") as handle:
                data = handle.read()
        except OSError as exc:
            self._emit("log", f"读取文件失败：{exc}")
            return
        if not self._connected:
            self._emit("log", "未连接，无法发送文件")
            return
        if ws.closed:
            self._emit("log", "会话未激活，无法发送文件")
            return
        outgoing = OutgoingFile(uuid_hex(), os.path.basename(path), data, direction="down")
        await ws.send_str(encode(outgoing.offer()))
        for header, chunk in outgoing.frames():
            await ws.send_str(encode(header))
            await ws.send_bytes(chunk)
        prefix = f"{reason}：" if reason else ""
        await self._notify_viewer(ws, f"{prefix}已发送文件 {os.path.basename(path)}（{len(data)} bytes）")

    def resolve_pull_path(self, raw: str) -> tuple[str | None, str]:
        """Validate a viewer-supplied path for a remote pull.

        Returns ``(path, "")`` when the request may proceed, otherwise ``(None, reason)``
        with a message meant for the viewer log. Only absolute paths to a single regular
        file inside the size cap are accepted; there is no directory packing yet, so a
        directory request is refused explicitly instead of silently doing nothing.
        """
        text = str(raw or "").strip().strip('"').strip("'")
        if not text:
            return None, "取文件请求缺少路径"
        if "~" in text or "%" in text:
            text = os.path.expandvars(os.path.expanduser(text))
        if not os.path.isabs(text):
            return None, f"只接受绝对路径：{raw}"
        if os.path.isdir(text):
            return None, f"暂不支持下载整个目录：{raw}"
        if not os.path.isfile(text):
            return None, f"文件不存在或不是普通文件：{raw}"
        try:
            size = os.path.getsize(text)
        except OSError as exc:
            return None, f"读取文件大小失败：{exc}"
        if size > MAX_PULL_BYTES:
            return None, (
                f"文件太大（{size} bytes，上限 {MAX_PULL_BYTES} bytes）：{raw}"
            )
        return text, ""

    async def _notify_viewer(
        self, ws: aiohttp.ClientWebSocketResponse, text: str
    ) -> None:
        """把取文件的结果回给主控端；本地日志只有被控端自己看得到。"""
        self._emit("log", text)
        if ws.closed:
            return
        try:
            await ws.send_str(encode({"t": "log", "text": text}))
        except (ConnectionResetError, RuntimeError):
            pass

    async def _serve_get(
        self, ws: aiohttp.ClientWebSocketResponse, message: dict
    ) -> None:
        if not self._active_session:
            await self._notify_viewer(ws, "没有活动会话，已拒绝远端取文件请求")
            return
        path, reason = self.resolve_pull_path(str(message.get("path", "")))
        if path is None:
            await self._notify_viewer(ws, f"远端取文件被拒绝：{reason}")
            return
        await self._push_file(ws, path, reason="远端取文件")

    def list_directory(self, raw: str) -> tuple[list[dict], str, str]:
        """List one directory for the viewer's remote file browser.

        具体规则在 ``common/listing.py``，这里只负责套上被控端的条数上限，这样
        主控端本地栏和被控端远端栏不会走出两套不同的行为。
        """
        return scan_directory(raw, limit=MAX_LIST_ENTRIES)

    async def _serve_list(
        self, ws: aiohttp.ClientWebSocketResponse, message: dict
    ) -> None:
        if not self._active_session:
            await self._notify_viewer(ws, "没有活动会话，已拒绝远端列目录请求")
            return
        entries, directory, error = self.list_directory(str(message.get("path", "")))
        payload = {
            "t": "meta",
            "kind": "file_list",
            "path": directory,
            "entries": entries,
            "error": error,
        }
        self._emit(
            "log",
            f"列目录被拒绝：{error}" if error else f"列出目录 {directory}（{len(entries)} 项）",
        )
        if ws.closed:
            return
        try:
            await ws.send_str(encode(payload))
        except (ConnectionResetError, RuntimeError):
            pass

    def _open_incoming(self, message: dict) -> str:
        """开一个入站文件；返回拒绝原因（空串表示已接受）。

        主控端可以带 ``dir`` 指定落到哪个远端目录（双击远端目录后上传就用它），
        不带就落到 ``download_dir``。目录必须是已存在的绝对路径，否则明确拒绝，
        免得文件悄悄落在别的地方。
        """
        transfer_id = str(message.get("id", ""))
        if message.get("direction") != "up":
            return ""
        target_dir = self.config.download_dir
        raw_dir = str(message.get("dir") or "").strip().strip('"').strip("'")
        if raw_dir:
            if not os.path.isabs(raw_dir) or not os.path.isdir(raw_dir):
                return f"上传被拒绝：目标目录无效：{raw_dir}"
            target_dir = raw_dir
        try:
            incoming = IncomingFile(
                transfer_id,
                str(message.get("name", "file.bin")),
                int(message.get("size", 0)),
                target_dir,
                message.get("sha256"),
            )
        except (FileTransferError, ValueError) as exc:
            return f"文件请求无效：{exc}"
        self._incoming[transfer_id] = incoming
        self._emit("log", f"接收文件：{incoming.name}（{incoming.expected_size} bytes）→ {target_dir}")
        return ""


def uuid_hex() -> str:
    import uuid

    return uuid.uuid4().hex[:10]


class AgentWindow:
    """Tk shell: connection settings, consent prompts and a live log."""

    def __init__(self) -> None:
        import tkinter as tk
        from tkinter import messagebox, scrolledtext, ttk

        self._tk = tk
        self._messagebox = messagebox
        self.config = AgentConfig.load()
        self.events: "queue.Queue" = queue.Queue()
        self.commands: "queue.Queue" = queue.Queue()
        self.worker = AgentWorker(self.config, self.events, self.commands)

        self.root = tk.Tk()
        self.root.title("远程桌面 · 被控端")
        self.root.geometry("900x680")
        self.root.minsize(800, 560)

        from ui.theme import AGENT_PAGES, PALETTE, apply_theme, format_ident

        apply_theme(self.root)
        self.root.configure(background=PALETTE["bg"])

        self.id_var = tk.StringVar(value=self.config.agent_id)
        self.name_var = tk.StringVar(value=self.config.name)
        self.url_var = tk.StringVar(value=self.config.relay_url)
        self.key_var = tk.StringVar(value=self.config.agent_key)
        self.allow_var = tk.BooleanVar(value=bool(self.config.allow_input))
        self.clip_var = tk.BooleanVar(value=bool(self.config.clipboard_sync))
        self.code_var = tk.StringVar(value=self.config.session_code)
        self.unattended_var = tk.BooleanVar(value=bool(self.config.unattended))
        self.policy_var = tk.StringVar(value=POLICY_LABELS[self.config.session_code_policy])
        self.id_display = tk.StringVar(value=format_ident(self.config.agent_id))
        self.code_display = tk.StringVar(value=format_ident(self.config.session_code))
        self.status_var = tk.StringVar(value="未启动")

        shell = ttk.Frame(self.root)
        shell.pack(fill="both", expand=True)

        side = ttk.Frame(shell, style="Sidebar.TFrame", width=188)
        side.pack(side="left", fill="y")
        side.pack_propagate(False)
        ttk.Label(side, text="EthanDesk", style="Sidebar.TLabel",
                  font=("Microsoft YaHei UI", 12, "bold")).pack(anchor="w", padx=18, pady=(20, 0))
        ttk.Label(side, text="被控端 · Free", style="SidebarMuted.TLabel").pack(anchor="w", padx=18, pady=(2, 22))

        self._nav_buttons = {}
        for name in AGENT_PAGES:
            button = ttk.Button(side, text=name, style="Nav.TButton",
                                command=lambda n=name: self._show_page(n))
            button.pack(fill="x", padx=8, pady=1)
            self._nav_buttons[name] = button

        foot = ttk.Frame(side, style="Sidebar.TFrame")
        foot.pack(side="bottom", fill="x", padx=18, pady=16)
        ttk.Label(foot, textvariable=self.status_var, style="SidebarMuted.TLabel",
                  wraplength=150, justify="left").pack(anchor="w")

        main = ttk.Frame(shell)
        main.pack(side="left", fill="both", expand=True)
        body = ttk.Frame(main)
        body.pack(fill="both", expand=True, padx=22, pady=18)

        self.pages = {
            "远程协助": self._page_assist(body),
            "设置": self._page_settings(body),
        }
        self._show_page("远程协助")

        self.root.protocol("WM_DELETE_WINDOW", self._on_close)
        self.root.after(80, self._drain_events)

    # ------------------------------------------------------------ page router
    def _show_page(self, name: str) -> None:
        pages = getattr(self, "pages", {})
        if not pages:
            return
        for key, frame in pages.items():
            if key == name:
                frame.pack(fill="both", expand=True)
            else:
                frame.pack_forget()
        for key, button in getattr(self, "_nav_buttons", {}).items():
            button.configure(style="NavOn.TButton" if key == name else "Nav.TButton")
        self.active_page = name

    def _page_assist(self, parent):
        tk = self._tk
        ttk = __import__("tkinter.ttk", fromlist=["ttk"])
        scrolledtext = __import__("tkinter.scrolledtext", fromlist=["scrolledtext"])
        from ui.theme import PALETTE

        page = ttk.Frame(parent)

        head = ttk.Frame(page)
        head.pack(fill="x")
        ttk.Label(head, text="远程协助本机", style="H1.TLabel").pack(side="left")
        ttk.Label(head, textvariable=self.status_var, style="Muted.TLabel").pack(side="right")

        ttk.Label(page, text="本机识别码", style="Muted.TLabel").pack(anchor="w", pady=(20, 0))
        id_row = ttk.Frame(page)
        id_row.pack(anchor="w", pady=(2, 0))
        ttk.Label(id_row, textvariable=self.id_display, style="Ident.TLabel").pack(side="left")
        ttk.Button(id_row, text="复制", style="Ghost.TButton", command=self._copy_id).pack(side="left", padx=12)

        ttk.Label(page, text="单次验证码", style="Muted.TLabel").pack(anchor="w", pady=(18, 0))
        code_row = ttk.Frame(page)
        code_row.pack(anchor="w", pady=(2, 0))
        ttk.Label(code_row, textvariable=self.code_display, style="Code.TLabel").pack(side="left")
        ttk.Button(code_row, text="换一个", command=self._rotate_code).pack(side="left", padx=12)

        options = ttk.Frame(page)
        options.pack(fill="x", pady=(18, 0))
        ttk.Checkbutton(options, text="无人值守（凭验证码免确认）", variable=self.unattended_var,
                        command=self._apply_code_policy).pack(side="left")
        ttk.Label(options, text="更新策略", style="Muted.TLabel").pack(side="left", padx=(24, 8))
        policy_box = ttk.Combobox(options, textvariable=self.policy_var, width=16, state="readonly",
                                  values=tuple(POLICY_LABELS.values()))
        policy_box.pack(side="left")
        policy_box.bind("<<ComboboxSelected>>", lambda _event: self._apply_code_policy())

        actions = ttk.Frame(page)
        actions.pack(fill="x", pady=(18, 10))
        self.connect_button = ttk.Button(actions, text="开始服务", style="Accent.TButton",
                                         command=self._start_service)
        self.connect_button.pack(side="left")
        self.stop_button = ttk.Button(actions, text="停止服务", command=self._stop_service, state="disabled")
        self.stop_button.pack(side="left", padx=8)
        ttk.Button(actions, text="向远控方发送文件…", command=self._pick_file).pack(side="left", padx=8)

        ttk.Label(page, text="日志", style="Section.TLabel").pack(anchor="w", pady=(10, 4))
        self.log = scrolledtext.ScrolledText(page, height=12, wrap="word", state="disabled",
                                             background=PALETTE["panel"], foreground=PALETTE["text"],
                                             insertbackground=PALETTE["text"], borderwidth=0,
                                             font=("Consolas", 9))
        self.log.pack(fill="both", expand=True)
        return page

    def _page_settings(self, parent):
        ttk = __import__("tkinter.ttk", fromlist=["ttk"])
        from ui.theme import PALETTE, format_ident

        page = ttk.Frame(parent)
        ttk.Label(page, text="设置", style="H1.TLabel").pack(anchor="w", pady=(4, 12))

        grid = ttk.Frame(page)
        grid.pack(fill="x")
        ttk.Label(grid, text="设备名称", style="Muted.TLabel").grid(row=0, column=0, sticky="w", pady=7)
        ttk.Entry(grid, textvariable=self.name_var, width=34).grid(row=0, column=1, sticky="w", padx=16)
        ttk.Label(grid, text="中继地址", style="Muted.TLabel").grid(row=1, column=0, sticky="w", pady=7)
        ttk.Entry(grid, textvariable=self.url_var, width=48).grid(row=1, column=1, sticky="w", padx=16)
        ttk.Label(grid, text="连接密钥", style="Muted.TLabel").grid(row=2, column=0, sticky="w", pady=7)
        ttk.Entry(grid, textvariable=self.key_var, width=48, show="*").grid(row=2, column=1, sticky="w", padx=16)
        ttk.Label(grid, text="更新策略", style="Muted.TLabel").grid(row=3, column=0, sticky="w", pady=7)
        ttk.Combobox(grid, textvariable=self.policy_var, width=16, state="readonly",
                     values=tuple(POLICY_LABELS.values())).grid(row=3, column=1, sticky="w", padx=16)
        ttk.Label(grid, text="下载目录", style="Muted.TLabel").grid(row=4, column=0, sticky="w", pady=7)
        ttk.Label(grid, text=self.config.download_dir, style="Muted.TLabel").grid(
            row=4, column=1, sticky="w", padx=16)

        checks = ttk.Frame(page)
        checks.pack(anchor="w", pady=(16, 0))
        ttk.Checkbutton(checks, text="允许鼠标键盘控制", variable=self.allow_var,
                        command=self._apply_settings).pack(side="left")
        ttk.Checkbutton(checks, text="同步剪贴板", variable=self.clip_var,
                        command=self._apply_settings).pack(side="left", padx=18)

        bar = ttk.Frame(page)
        bar.pack(anchor="w", pady=(18, 0))
        ttk.Button(bar, text="保存配置", style="Accent.TButton",
                   command=self._save_window_config).pack(side="left")
        return page

    def _refresh_code_display(self) -> None:
        from ui.theme import format_ident

        self.code_display.set(format_ident(self.config.session_code))


    # ------------------------------------------------------------ ui helpers
    def _copy_id(self) -> None:
        self.root.clipboard_clear()
        self.root.clipboard_append(self.id_var.get())

    def _append_log(self, text: str) -> None:
        self.log.configure(state="normal")
        self.log.insert("end", f"{time.strftime('%H:%M:%S')}  {text}\n")
        self.log.see("end")
        self.log.configure(state="disabled")

    def _rotate_code(self) -> None:
        self.config.rotate_session_code()
        self.code_var.set(self.config.session_code)
        self._refresh_code_display()
        self._save_window_config()

    def _apply_code_policy(self) -> None:
        self.config.session_code_policy = POLICY_BY_LABEL.get(
            self.policy_var.get(), POLICY_EVERY_SESSION
        )
        self.config.unattended = bool(self.unattended_var.get())
        self._save_window_config()

    def _save_window_config(self) -> None:
        try:
            self.config.save()
        except OSError as exc:
            self._append_log(f"保存配置失败：{exc}")

    def _apply_settings(self) -> None:
        self.worker.apply_settings(
            allow_input=self.allow_var.get(),
            clipboard_sync=self.clip_var.get(),
        )

    # ------------------------------------------------------------ actions
    def _start_service(self) -> None:
        self.config.relay_url = self.url_var.get().strip()
        self.config.agent_id = self.id_var.get().strip()
        self.config.name = self.name_var.get().strip() or self.config.agent_id
        self.config.agent_key = self.key_var.get().strip()
        self.config.allow_input = self.allow_var.get()
        self.config.clipboard_sync = self.clip_var.get()
        try:
            self.config.save()
        except OSError as exc:
            self._append_log(f"保存配置失败：{exc}")
        self._apply_settings()
        self.worker.start()
        self.connect_button.configure(state="disabled")
        self.stop_button.configure(state="normal")
        self.status_var.set("正在连接中继…")

    def _stop_service(self) -> None:
        self.worker.stop()
        self.connect_button.configure(state="normal")
        self.stop_button.configure(state="disabled")
        self.status_var.set("已停止")
        self._append_log("服务已停止")

    def _pick_file(self) -> None:
        from tkinter import filedialog

        path = filedialog.askopenfilename(title="选择要发送给远控方的文件")
        if path:
            self.worker.send_file(path)

    # ------------------------------------------------------------ event pump
    def _drain_events(self) -> None:
        while True:
            try:
                kind, payload = self.events.get_nowait()
            except queue.Empty:
                break
            if kind == "log":
                self._append_log(str(payload))
            elif kind == "status":
                self.status_var.set({"online": "在线，等待远控请求", "offline": "离线", "connecting": "连接中…"}.get(str(payload), str(payload)))
            elif kind == "consent_request":
                self._prompt_consent(payload)
        self.root.after(80, self._drain_events)

    def _prompt_consent(self, payload: dict) -> None:
        viewer = payload.get("viewer", "未知用户")
        accepted = self._messagebox.askyesno(
            "远程控制请求",
            f"{viewer} 请求控制本机。\n\n允许本次会话吗？",
            parent=self.root,
        )
        self.worker.answer_consent(str(payload.get("sid", "")), bool(accepted))

    def _on_close(self) -> None:
        self.worker.stop()
        self.root.destroy()

    def run(self) -> None:
        self.root.mainloop()


def main() -> None:
    # Must happen before the first capture and before Tk builds its widgets: without it
    # the process sees a logical desktop (1536x864 at 125%) while mss grabs physical
    # pixels (1920x1080), and every remote click lands 20% short of the intended point.
    from common.dpi import ensure_dpi_aware

    ensure_dpi_aware()
    AgentWindow().run()


if __name__ == "__main__":
    main()
