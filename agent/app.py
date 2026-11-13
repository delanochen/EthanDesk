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

from agent import clip, inject, screen
from agent.config import AgentConfig
from agent.files_transfer import FileTransferError, IncomingFile, OutgoingFile
from common.protocol import BinaryPairer, ProtocolError, decode, encode, make_bin_header

CONSENT_TIMEOUT = 30.0
CLIPBOARD_POLL = 0.6
PING_INTERVAL = 20.0
RECONNECT_DELAY = 3.0


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
        self._active_session: str | None = None
        self._capture_task: asyncio.Task | None = None
        self._consent: dict[str, asyncio.Future] = {}
        self._incoming: dict[str, IncomingFile] = {}
        self._last_clip_signature = ""
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
        try:
            while not ws.closed:
                await ws.send_str(encode({"t": "ping", "ts": time.time()}))
                await asyncio.sleep(PING_INTERVAL)
        except (ConnectionResetError, asyncio.CancelledError):
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
            self._emit("log", "远控会话已结束")
            return
        if kind == "input":
            if self._allow_input and self._active_session:
                try:
                    inject.apply_input(message, self._capturer.bounds if self._capturer else None)
                except inject.InputInjectionError as exc:
                    self._emit("log", f"输入注入失败：{exc}")
            return
        if kind == "set_quality":
            self.apply_settings(
                quality=message.get("quality", self._quality),
                scale=message.get("scale", self._scale),
                fps=message.get("fps", self._fps),
            )
            return
        if kind == "frame_recv":
            self._inflight = 0
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
            self._open_incoming(message)
            return
        if kind == "bin":
            self._pairer.header(message)
            return

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

    async def _ask_consent(self, ws: aiohttp.ClientWebSocketResponse, message: dict) -> None:
        session_id = str(message.get("sid", ""))
        viewer = str(message.get("viewer", "viewer"))
        if self._active_session:
            await ws.send_str(encode({"t": "session_denied", "sid": session_id}))
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

    async def _on_session_start(self, ws: aiohttp.ClientWebSocketResponse) -> None:
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
        self._capture_task = asyncio.create_task(self._capture_loop(ws))

    def _stop_capture(self) -> None:
        if self._capture_task and not self._capture_task.done():
            self._capture_task.cancel()
        self._capture_task = None
        self._active_session = None
        self._inflight = 0

    async def _capture_loop(self, ws: aiohttp.ClientWebSocketResponse) -> None:
        capturer = self._capturer
        if capturer is None:
            return
        sequence = 0
        try:
            while not ws.closed:
                if self._inflight > 0:
                    await asyncio.sleep(0.004)
                    continue
                interval = 1.0 / max(1, self._fps)
                started = time.perf_counter()
                try:
                    payload, width, height = capturer.capture(self._scale, self._quality)
                except OSError as exc:
                    self._emit("log", f"抓屏失败：{exc}")
                    await asyncio.sleep(0.5)
                    continue
                header = make_bin_header(
                    "frame", seq=sequence, size=len(payload), width=width, height=height
                )
                await ws.send_str(encode(header))
                await ws.send_bytes(payload)
                self._inflight += 1
                sequence += 1
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
        try:
            with open(path, "rb") as handle:
                data = handle.read()
        except OSError as exc:
            self._emit("log", f"读取文件失败：{exc}")
            return
        if not self._connected:
            self._emit("log", "未连接，无法发送文件")
            return
        ws = getattr(self, "_ws", None)
        if ws is None or ws.closed:
            self._emit("log", "会话未激活，无法发送文件")
            return
        outgoing = OutgoingFile(uuid_hex(), os.path.basename(path), data, direction="down")
        await ws.send_str(encode(outgoing.offer()))
        for header, chunk in outgoing.frames():
            await ws.send_str(encode(header))
            await ws.send_bytes(chunk)
        self._emit("log", f"已发送文件：{os.path.basename(path)}")

    def _open_incoming(self, message: dict) -> None:
        transfer_id = str(message.get("id", ""))
        if message.get("direction") != "up":
            return
        try:
            incoming = IncomingFile(
                transfer_id,
                str(message.get("name", "file.bin")),
                int(message.get("size", 0)),
                self.config.download_dir,
                message.get("sha256"),
            )
        except (FileTransferError, ValueError) as exc:
            self._emit("log", f"文件请求无效：{exc}")
            return
        self._incoming[transfer_id] = incoming
        self._emit("log", f"接收文件：{incoming.name}（{incoming.expected_size} bytes）")


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
        self.root.geometry("640x520")
        self.root.minsize(520, 420)

        frame = ttk.Frame(self.root, padding=10)
        frame.pack(fill="both", expand=True)

        self.id_var = tk.StringVar(value=self.config.agent_id)
        self.name_var = tk.StringVar(value=self.config.name)
        self.url_var = tk.StringVar(value=self.config.relay_url)
        self.key_var = tk.StringVar(value=self.config.agent_key)
        self.allow_var = tk.BooleanVar(value=self.config.allow_input)
        self.clip_var = tk.BooleanVar(value=self.config.clipboard_sync)

        ttk.Label(frame, text="本机 ID").grid(row=0, column=0, sticky="w")
        ttk.Entry(frame, textvariable=self.id_var, width=14, state="readonly").grid(row=0, column=1, sticky="w")
        copy_button = ttk.Button(frame, text="复制", width=6, command=self._copy_id)
        copy_button.grid(row=0, column=2, sticky="w", padx=6)

        ttk.Label(frame, text="设备名称").grid(row=1, column=0, sticky="w")
        ttk.Entry(frame, textvariable=self.name_var, width=28).grid(row=1, column=1, columnspan=2, sticky="we")

        ttk.Label(frame, text="中继地址").grid(row=2, column=0, sticky="w")
        ttk.Entry(frame, textvariable=self.url_var, width=42).grid(row=2, column=1, columnspan=2, sticky="we")

        ttk.Label(frame, text="连接密钥").grid(row=3, column=0, sticky="w")
        ttk.Entry(frame, textvariable=self.key_var, width=42, show="*").grid(row=3, column=1, columnspan=2, sticky="we")

        options = ttk.Frame(frame)
        options.grid(row=4, column=0, columnspan=3, sticky="w", pady=(6, 6))
        ttk.Checkbutton(
            options, text="允许鼠标键盘控制", variable=self.allow_var, command=self._apply_settings
        ).pack(side="left", padx=(0, 16))
        ttk.Checkbutton(
            options, text="同步剪贴板", variable=self.clip_var, command=self._apply_settings
        ).pack(side="left")

        buttons = ttk.Frame(frame)
        buttons.grid(row=5, column=0, columnspan=3, sticky="w", pady=(0, 8))
        self.connect_button = ttk.Button(buttons, text="开始服务", command=self._start_service)
        self.connect_button.pack(side="left")
        self.stop_button = ttk.Button(buttons, text="停止服务", command=self._stop_service, state="disabled")
        self.stop_button.pack(side="left", padx=6)
        ttk.Button(buttons, text="向远控方发送文件…", command=self._pick_file).pack(side="left", padx=6)

        self.status_var = tk.StringVar(value="未启动")
        status = ttk.Label(frame, textvariable=self.status_var, foreground="#1a5d1a")
        status.grid(row=6, column=0, columnspan=3, sticky="w", pady=(0, 6))

        self.log = scrolledtext.ScrolledText(frame, height=14, wrap="word", state="disabled")
        self.log.grid(row=7, column=0, columnspan=3, sticky="nsew")
        frame.columnconfigure(1, weight=1)
        frame.rowconfigure(7, weight=1)

        self.root.protocol("WM_DELETE_WINDOW", self._on_close)
        self.root.after(80, self._drain_events)

    # ------------------------------------------------------------ ui helpers
    def _copy_id(self) -> None:
        self.root.clipboard_clear()
        self.root.clipboard_append(self.id_var.get())

    def _append_log(self, text: str) -> None:
        self.log.configure(state="normal")
        self.log.insert("end", f"{time.strftime('%H:%M:%S')}  {text}\n")
        self.log.see("end")
        self.log.configure(state="disabled")

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
    AgentWindow().run()


if __name__ == "__main__":
    main()
