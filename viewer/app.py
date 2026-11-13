"""Windows viewer: device console, live picture, mouse/keyboard, files and clipboard.

Same split as the agent: :class:`ViewerWorker` owns asyncio and the relay session, the Tk
thread owns widgets. Communication is one queue each way, so no Tk call ever happens off
the main thread.
"""

from __future__ import annotations

import asyncio
import io
import os
import queue
import threading
import time
from typing import Any

from agent.files_transfer import FileTransferError, IncomingFile, OutgoingFile
from common.coords import canvas_to_frame_norm, letterbox_fit
from common.protocol import BinaryPairer, encode, make_bin_header
from viewer.client import DeskClient, DeskClientError, Session

COMMAND_POLL = 0.01
AGENT_REFRESH = 5.0
CLIPBOARD_POLL = 0.6
DEFAULT_DOWNLOAD_DIR = os.path.join(os.path.expanduser("~"), "Downloads", "deskrelay")


class ViewerWorker:
    def __init__(self, events: "queue.Queue", commands: "queue.Queue") -> None:
        self.events = events
        self.commands = commands
        self._loop: asyncio.AbstractEventLoop | None = None
        self._thread: threading.Thread | None = None
        self._client: DeskClient | None = None
        self._session: Session | None = None
        self._pairer = BinaryPairer()
        self._stopping = False
        self._incoming: dict[str, IncomingFile] = {}
        self._last_clip = ""
        self._clip_enabled = False
        self._connected_agent = ""
        self.download_dir = DEFAULT_DOWNLOAD_DIR

    # ---------------------------------------------------------------- lifecycle
    def start(self) -> None:
        if self._thread and self._thread.is_alive():
            return
        self._thread = threading.Thread(target=self._run, name="deskrelay-viewer", daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._stopping = True
        loop = self._loop
        if loop and not loop.is_closed():
            loop.call_soon_threadsafe(lambda: None)

    def _run(self) -> None:
        asyncio.run(self._main())

    def _emit(self, kind: str, payload: Any = None) -> None:
        self.events.put((kind, payload))

    async def _main(self) -> None:
        self._loop = asyncio.get_running_loop()
        refresh_task = asyncio.create_task(self._refresh_loop())
        clip_task = asyncio.create_task(self._clipboard_loop())
        try:
            while not self._stopping:
                command = None
                try:
                    command = self.commands.get_nowait()
                except queue.Empty:
                    await asyncio.sleep(COMMAND_POLL)
                    continue
                await self._dispatch(command)
        finally:
            refresh_task.cancel()
            clip_task.cancel()
            await self._teardown()

    async def _dispatch(self, command: tuple[str, Any]) -> None:
        action, payload = command
        try:
            if action == "login":
                await self._login(payload["url"], payload["password"])
            elif action == "refresh_agents":
                await self._list_agents()
            elif action == "refresh_sessions":
                await self._list_sessions()
            elif action == "connect":
                await self._connect(str(payload))
            elif action == "disconnect":
                await self._teardown()
            elif action == "input" and self._session is not None:
                await self._session.send_text(payload)
            elif action == "frame_recv" and self._session is not None:
                await self._session.send_text({"t": "frame_recv"})
            elif action == "settings" and self._session is not None:
                await self._session.send_text({"t": "set_quality", **payload})
            elif action == "upload" and self._session is not None:
                await self._upload(str(payload))
            elif action == "clip" and self._session is not None:
                await self._session.send_text({"t": "clip", "dir": "to_agent", "text": str(payload)})
            elif action == "set_clipboard_sync":
                self._clip_enabled = bool(payload)
            elif action == "shutdown":
                self._stopping = True
        except DeskClientError as exc:
            self._emit("error", str(exc))
        except Exception as exc:  # surface unexpected faults instead of dying silently
            self._emit("error", f"内部错误：{exc!r}")

    # ---------------------------------------------------------------- client
    async def _login(self, url: str, password: str) -> None:
        if self._client is not None:
            await self._client.close()
        self._client = DeskClient(url, password=password)
        await self._client.login()
        self._emit("logged_in", url)
        self._emit("log", "登录成功")
        await self._list_agents()

    async def _list_agents(self) -> None:
        if self._client is None:
            return
        agents = await self._client.list_agents()
        self._emit("agents", agents)

    async def _list_sessions(self) -> None:
        if self._client is None:
            return
        sessions = await self._client.list_sessions()
        self._emit("sessions", sessions)

    async def _refresh_loop(self) -> None:
        try:
            while True:
                await asyncio.sleep(AGENT_REFRESH)
                if self._client is None:
                    continue
                if self._session is None:
                    await self._list_agents()
        except asyncio.CancelledError:
            pass

    async def _connect(self, agent_id: str) -> None:
        if self._client is None:
            self._emit("error", "请先登录")
            return
        await self._teardown()
        self._emit("status", f"正在连接 {agent_id}…")
        session = await self._client.open_session(agent_id)
        self._session = session
        self._pairer = BinaryPairer()
        self._incoming = {}
        self._connected_agent = agent_id
        self._emit("session_open", agent_id)
        asyncio.create_task(self._receive_loop(session))

    async def _receive_loop(self, session: Session) -> None:
        try:
            while True:
                kind, payload = await session.receive()
                if kind == "closed":
                    break
                if kind == "text":
                    if isinstance(payload, dict):
                        await self._handle_text(payload)
                    continue
                if kind == "bin" and isinstance(payload, bytes):
                    self._handle_binary(payload)
        except asyncio.CancelledError:
            pass
        except Exception as exc:
            self._emit("error", f"会话中断：{exc!r}")
        finally:
            if self._session is session:
                self._session = None
            self._emit("session_closed", self._connected_agent)

    async def _handle_text(self, message: dict) -> None:
        kind = message.get("t")
        if kind == "bin":
            self._pairer.header(message)
            return
        if kind == "meta":
            self._emit("meta", message)
            return
        if kind == "frame_recv":
            return
        if kind == "clip":
            text = str(message.get("text", ""))
            self._last_clip = text
            self._emit("clip", text)
            return
        if kind == "file_offer":
            self._open_incoming(message)
            return
        if kind == "file_done":
            self._emit("log", "文件传输完成")
            return
        if kind == "error":
            self._emit("error", f"中继错误：{message.get('code')}")
            return
        if kind == "log":
            self._emit("log", str(message.get("text", "")))

    def _handle_binary(self, data: bytes) -> None:
        try:
            header, payload = self._pairer.attach(data)
        except Exception:
            return
        kind = header.get("kind")
        if kind == "frame":
            self._emit("frame", {"header": header, "data": payload})
            return
        if kind != "file":
            return
        transfer_id = str(header.get("id", ""))
        incoming = self._incoming.get(transfer_id)
        if incoming is None:
            return
        try:
            incoming.feed(int(header.get("seq", 0)), payload)
            if incoming.received >= incoming.expected_size:
                path = incoming.finish()
                self._emit("file_saved", path)
                self._incoming.pop(transfer_id, None)
        except (FileTransferError, OSError) as exc:
            self._emit("error", f"文件接收失败：{exc}")
            self._incoming.pop(transfer_id, None)

    def _open_incoming(self, message: dict) -> None:
        if message.get("direction") != "down":
            return
        transfer_id = str(message.get("id", ""))
        try:
            self._incoming[transfer_id] = IncomingFile(
                transfer_id,
                str(message.get("name", "file.bin")),
                int(message.get("size", 0)),
                self.download_dir,
                message.get("sha256"),
            )
            self._emit("log", f"开始接收 {message.get('name')}")
        except (FileTransferError, ValueError) as exc:
            self._emit("error", f"文件请求无效：{exc}")

    async def _upload(self, path: str) -> None:
        session = self._session
        if session is None:
            return
        try:
            with open(path, "rb") as handle:
                data = handle.read()
        except OSError as exc:
            self._emit("error", f"读取文件失败：{exc}")
            return
        outgoing = OutgoingFile(os.urandom(6).hex(), os.path.basename(path), data, direction="up")
        await session.send_text(outgoing.offer())
        for header, chunk in outgoing.frames():
            await session.send_bin(header, chunk)
        self._emit("log", f"已上传：{os.path.basename(path)}")

    async def _clipboard_loop(self) -> None:
        try:
            while True:
                await asyncio.sleep(CLIPBOARD_POLL)
                if not self._clip_enabled or self._session is None:
                    continue
                self._emit("clip_poll", None)
        except asyncio.CancelledError:
            pass

    def note_local_clipboard(self, text: str) -> bool:
        """Return True when local clipboard differs from the last synced value."""
        if text == self._last_clip:
            return False
        self._last_clip = text
        return True

    async def _teardown(self) -> None:
        session, self._session = self._session, None
        if session is not None:
            await session.close()
        self._connected_agent = ""


class ViewerWindow:
    def __init__(self) -> None:
        import tkinter as tk
        from tkinter import messagebox, ttk

        self._tk = tk
        self._messagebox = messagebox
        self.events: "queue.Queue" = queue.Queue()
        self.commands: "queue.Queue" = queue.Queue()
        self.worker = ViewerWorker(self.events, self.commands)
        self.worker.start()

        self.frame_image = None
        self.frame_size = (0, 0)
        self.last_frame_at = 0.0
        self.fps_counter = 0
        self.fps_window_started = time.time()
        self.current_fps = 0.0
        self.clipboard_enabled = False
        self.last_clip_text = ""
        self._pending_move: tuple[float, float] | None = None

        self.root = tk.Tk()
        self.root.title("远程桌面 · 主控端")
        self.root.geometry("1180x760")
        self.root.minsize(900, 600)

        self.url_var = tk.StringVar(value="https://desk.example.com")
        self.password_var = tk.StringVar()
        self.quality_var = tk.IntVar(value=70)
        self.scale_var = tk.DoubleVar(value=1.0)
        self.fps_var = tk.IntVar(value=15)
        self.clip_var = tk.BooleanVar(value=False)
        self.status_var = tk.StringVar(value="未登录")
        self.metric_var = tk.StringVar(value="")
        self.login_info = tk.StringVar(value="")

        self._build_layout()
        self.root.after(30, self._drain_events)
        self.root.protocol("WM_DELETE_WINDOW", self._on_close)

    # ---------------------------------------------------------------- layout
    def _build_layout(self) -> None:
        from tkinter import ttk

        tk = self._tk
        outer = ttk.Frame(self.root, padding=8)
        outer.pack(fill="both", expand=True)

        login = ttk.LabelFrame(outer, text="登录中继", padding=8)
        login.pack(fill="x")
        ttk.Label(login, text="中继地址").grid(row=0, column=0, sticky="w")
        ttk.Entry(login, textvariable=self.url_var, width=34).grid(row=0, column=1, sticky="w", padx=6)
        ttk.Label(login, text="口令").grid(row=0, column=2, sticky="w")
        ttk.Entry(login, textvariable=self.password_var, show="*", width=18).grid(
            row=0, column=3, sticky="w", padx=6
        )
        ttk.Button(login, text="登录", command=self._login).grid(row=0, column=4, padx=4)
        ttk.Label(login, textvariable=self.login_info, foreground="#555").grid(
            row=1, column=0, columnspan=5, sticky="w", pady=(6, 0)
        )

        body = ttk.Panedwindow(outer, orient="horizontal")
        body.pack(fill="both", expand=True, pady=8)

        left = ttk.Frame(body, width=280)
        body.add(left, weight=0)
        ttk.Label(left, text="在线设备").pack(anchor="w")
        self.tree = ttk.Treeview(
            left, columns=("name", "host", "status"), show="headings", height=16, selectmode="browse"
        )
        self.tree.heading("name", text="名称")
        self.tree.heading("host", text="主机")
        self.tree.heading("status", text="状态")
        self.tree.column("name", width=120)
        self.tree.column("host", width=110)
        self.tree.column("status", width=60)
        self.tree.pack(fill="both", expand=True, pady=4)
        self.tree.bind("<Double-1>", lambda _event: self._connect_selected())

        buttons = ttk.Frame(left)
        buttons.pack(fill="x")
        ttk.Button(buttons, text="刷新", command=lambda: self.commands.put(("refresh_agents", None))).pack(
            side="left"
        )
        ttk.Button(buttons, text="连接", command=self._connect_selected).pack(side="left", padx=6)
        ttk.Button(buttons, text="断开", command=self._disconnect).pack(side="left")

        right = ttk.Frame(body)
        body.add(right, weight=1)

        tools = ttk.Frame(right)
        tools.pack(fill="x")
        ttk.Label(tools, text="画质").pack(side="left")
        ttk.Scale(tools, from_=20, to=95, variable=self.quality_var, orient="horizontal", length=110,
                  command=lambda _e: self._send_settings()).pack(side="left", padx=4)
        ttk.Label(tools, text="缩放").pack(side="left")
        ttk.Scale(tools, from_=0.25, to=1.0, variable=self.scale_var, orient="horizontal", length=110,
                  command=lambda _e: self._send_settings()).pack(side="left", padx=4)
        ttk.Label(tools, text="帧率").pack(side="left")
        ttk.Spinbox(tools, from_=1, to=30, textvariable=self.fps_var, width=4,
                    command=self._send_settings).pack(side="left", padx=4)
        ttk.Checkbutton(tools, text="剪贴板同步", variable=self.clip_var,
                        command=self._toggle_clipboard).pack(side="left", padx=8)
        ttk.Button(tools, text="上传文件…", command=self._pick_upload).pack(side="left", padx=4)

        self.canvas = tk.Canvas(right, background="#1c1c1c", highlightthickness=0, takefocus=1)
        self.canvas.pack(fill="both", expand=True, pady=6)
        self.canvas.bind("<Motion>", self._on_motion)
        self.canvas.bind("<ButtonPress-1>", lambda _e: self._send_button("left", True))
        self.canvas.bind("<ButtonRelease-1>", lambda _e: self._send_button("left", False))
        self.canvas.bind("<ButtonPress-3>", lambda _e: self._send_button("right", True))
        self.canvas.bind("<ButtonRelease-3>", lambda _e: self._send_button("right", False))
        self.canvas.bind("<ButtonPress-2>", lambda _e: self._send_button("middle", True))
        self.canvas.bind("<ButtonRelease-2>", lambda _e: self._send_button("middle", False))
        self.canvas.bind("<MouseWheel>", self._on_wheel)
        self.canvas.bind("<Button-4>", lambda _e: self._send_wheel(120))
        self.canvas.bind("<Button-5>", lambda _e: self._send_wheel(-120))
        self.canvas.bind("<KeyPress>", lambda event: self._on_key(event, True))
        self.canvas.bind("<KeyRelease>", lambda event: self._on_key(event, False))
        self.canvas.bind("<Configure>", lambda _event: self._redraw())

        status = ttk.Frame(right)
        status.pack(fill="x")
        ttk.Label(status, textvariable=self.status_var).pack(side="left")
        ttk.Label(status, textvariable=self.metric_var, foreground="#555").pack(side="right")

        history = ttk.LabelFrame(outer, text="会话记录", padding=6)
        history.pack(fill="x")
        self.history = ttk.Treeview(
            history, columns=("agent", "viewer", "start", "duration", "frames"), show="headings", height=4
        )
        for column, label, width in (
            ("agent", "设备", 120),
            ("viewer", "操作人", 90),
            ("start", "开始时间", 150),
            ("duration", "时长(秒)", 80),
            ("frames", "帧数", 70),
        ):
            self.history.heading(column, text=label)
            self.history.column(column, width=width)
        self.history.pack(fill="x")
        history_buttons = ttk.Frame(history)
        history_buttons.pack(fill="x", pady=(4, 0))
        ttk.Button(history_buttons, text="刷新记录",
                   command=lambda: self.commands.put(("refresh_sessions", None))).pack(side="left")

    # ---------------------------------------------------------------- actions
    def _login(self) -> None:
        url = self.url_var.get().strip()
        password = self.password_var.get()
        if not url:
            self._messagebox.showerror("缺少地址", "请填写中继地址")
            return
        self.status_var.set("正在登录…")
        self.commands.put(("login", {"url": url, "password": password}))

    def _connect_selected(self) -> None:
        selection = self.tree.selection()
        if not selection:
            self._messagebox.showinfo("未选择设备", "请先在左侧选择一个在线设备")
            return
        agent_id = selection[0]
        self.commands.put(("connect", agent_id))
        self.status_var.set("等待被控端确认…")

    def _disconnect(self) -> None:
        self.commands.put(("disconnect", None))
        self.status_var.set("已断开")

    def _send_settings(self) -> None:
        self.commands.put(
            (
                "settings",
                {
                    "quality": int(self.quality_var.get()),
                    "scale": round(float(self.scale_var.get()), 3),
                    "fps": int(self.fps_var.get()),
                },
            )
        )

    def _toggle_clipboard(self) -> None:
        self.clipboard_enabled = bool(self.clip_var.get())
        self.commands.put(("set_clipboard_sync", self.clipboard_enabled))
        if self.clipboard_enabled:
            self.last_clip_text = self._read_clipboard()

    def _pick_upload(self) -> None:
        from tkinter import filedialog

        path = filedialog.askopenfilename(title="选择要上传到被控端的文件")
        if path:
            self.commands.put(("upload", path))

    # ---------------------------------------------------------------- input
    def _frame_norm(self, event_x: float, event_y: float) -> tuple[float, float]:
        width, height = self.frame_size
        if width <= 0 or height <= 0:
            return 0.0, 0.0
        return canvas_to_frame_norm(
            event_x, event_y, width, height, self.canvas.winfo_width(), self.canvas.winfo_height()
        )

    def _on_motion(self, event) -> None:
        xn, yn = self._frame_norm(event.x, event.y)
        self._pending_move = (xn, yn)
        self.commands.put(("input", {"t": "input", "ev": "mouse_move", "x": xn, "y": yn}))

    def _send_button(self, button: str, down: bool) -> None:
        self.canvas.focus_set()
        self.commands.put(
            (
                "input",
                {"t": "input", "ev": "mouse_down" if down else "mouse_up", "button": button},
            )
        )

    def _on_wheel(self, event) -> None:
        self._send_wheel(120 if event.delta > 0 else -120)

    def _send_wheel(self, delta: int) -> None:
        self.commands.put(("input", {"t": "input", "ev": "wheel", "delta": delta}))

    def _on_key(self, event, down: bool) -> None:
        keysym = getattr(event, "keysym", "")
        if not keysym:
            return "break"
        self.commands.put(
            ("input", {"t": "input", "ev": "key_down" if down else "key_up", "keysym": keysym})
        )
        return "break"

    # ---------------------------------------------------------------- clipboard
    def _read_clipboard(self) -> str:
        try:
            return self.root.clipboard_get()
        except Exception:
            return ""

    def _write_clipboard(self, text: str) -> None:
        try:
            self.root.clipboard_clear()
            self.root.clipboard_append(text)
        except Exception:
            pass

    def _poll_clipboard(self) -> None:
        if not self.clipboard_enabled:
            return
        current = self._read_clipboard()
        if not current or current == self.last_clip_text:
            return
        self.last_clip_text = current
        self.commands.put(("clip", current))

    # ---------------------------------------------------------------- rendering
    def _redraw(self) -> None:
        if self.frame_image is None:
            return
        from PIL import ImageTk

        width = max(1, self.canvas.winfo_width())
        height = max(1, self.canvas.winfo_height())
        image = self.frame_image
        frame_w, frame_h = image.size
        _, _, draw_w, draw_h = letterbox_fit(frame_w, frame_h, width, height)
        if draw_w <= 0 or draw_h <= 0:
            return
        resized = image.resize((draw_w, draw_h))
        self._photo = ImageTk.PhotoImage(resized)
        self.canvas.delete("frame")
        self.canvas.create_image(width // 2, height // 2, image=self._photo, tags="frame")

    def _render_frame(self, payload: dict) -> None:
        from PIL import Image

        header = payload.get("header", {})
        data = payload.get("data", b"")
        try:
            image = Image.open(io.BytesIO(data))
            image.load()
        except (OSError, ValueError):
            return
        self.frame_image = image.convert("RGB")
        self.frame_size = (int(header.get("width") or image.width), int(header.get("height") or image.height))
        self.last_frame_at = time.time()
        self.fps_counter += 1
        now = time.time()
        if now - self.fps_window_started >= 1.0:
            self.current_fps = self.fps_counter / (now - self.fps_window_started)
            self.fps_counter = 0
            self.fps_window_started = now
            self.metric_var.set(f"{self.frame_size[0]}×{self.frame_size[1]}  {self.current_fps:.1f} fps")
        self._redraw()
        self.commands.put(("frame_recv", None))

    # ---------------------------------------------------------------- event pump
    def _drain_events(self) -> None:
        from tkinter import ttk

        for _ in range(200):
            try:
                kind, payload = self.events.get_nowait()
            except queue.Empty:
                break
            if kind == "agents":
                self._render_agents(payload or [])
            elif kind == "sessions":
                self._render_sessions(payload or [])
            elif kind == "frame":
                self._render_frame(payload)
            elif kind == "meta":
                self.status_var.set(f"已连接 · {payload.get('width')}×{payload.get('height')}")
                self._send_settings()
            elif kind == "clip":
                self.last_clip_text = str(payload)
                self._write_clipboard(str(payload))
            elif kind == "clip_poll":
                self._poll_clipboard()
            elif kind == "file_saved":
                self._messagebox.showinfo("文件已接收", f"已保存到：{payload}")
            elif kind == "session_open":
                self.status_var.set(f"已连接到 {payload}")
            elif kind == "session_closed":
                self.status_var.set("会话已结束")
                self.frame_image = None
                self.canvas.delete("frame")
            elif kind == "logged_in":
                self.login_info.set(f"已登录：{payload}")
            elif kind == "error":
                self.status_var.set(f"错误：{payload}")
                self._messagebox.showerror("操作失败", str(payload))
            elif kind == "log":
                self.status_var.set(str(payload))
        self.root.after(30, self._drain_events)

    def _render_agents(self, agents: list) -> None:
        existing = set(self.tree.get_children())
        seen = set()
        for agent in agents:
            agent_id = str(agent.get("id", ""))
            seen.add(agent_id)
            values = (
                agent.get("name", ""),
                agent.get("host", ""),
                "在线" if agent.get("online") else "离线",
            )
            if agent_id in existing:
                self.tree.item(agent_id, values=values)
            else:
                self.tree.insert("", "end", iid=agent_id, values=values)
        for stale in existing - seen:
            self.tree.delete(stale)

    def _render_sessions(self, sessions: list) -> None:
        for row in self.history.get_children():
            self.history.delete(row)
        for session in sessions:
            started = session.get("started_at") or 0
            ended = session.get("ended_at") or started
            duration = int(max(0, ended - started)) if started else 0
            self.history.insert(
                "",
                "end",
                values=(
                    session.get("agent_id", ""),
                    session.get("viewer", ""),
                    time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(started)) if started else "",
                    duration,
                    session.get("frames", 0),
                ),
            )

    def _on_close(self) -> None:
        self.commands.put(("shutdown", None))
        self.root.destroy()

    def run(self) -> None:
        self.root.mainloop()


def main() -> None:
    ViewerWindow().run()


if __name__ == "__main__":
    main()
