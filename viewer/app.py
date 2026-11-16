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
import webbrowser
from typing import Any

from agent.files_transfer import FileTransferError, IncomingFile, OutgoingFile
from common.coords import canvas_to_frame_norm, letterbox_fit
from common.keys import text_for_key
from common.protocol import BinaryPairer, encode, make_bin_header
from common.defaults import DEFAULT_VIEWER_URL
from common.update import check_update
from common.version import APP_NAME, app_version
from viewer.client import DeskClient, DeskClientError, Session
from viewer.config import ViewerConfig
from viewer.tray import TrayController, TrayUnavailable

COMMAND_POLL = 0.01
AGENT_REFRESH = 5.0
CLIPBOARD_POLL = 0.6
DEFAULT_DOWNLOAD_DIR = os.path.join(os.path.expanduser("~"), "Downloads", "deskrelay")

# Adaptive picture control (自适应画质): trade sharpness for frame rate on a slow link.
# The low and high thresholds are deliberately far apart, so a marginal link sits between
# them instead of oscillating on every measurement window.
ADAPT_WINDOW = 4.0
ADAPT_MIN_SCALE = 0.5
ADAPT_MIN_QUALITY = 35
ADAPT_MAX_QUALITY = 85
ADAPT_LOW_FPS = 3.0
# 升档阈值取目标帧率的八成（15 fps 目标 ≈ 12），原来卡在 14 fps 时，档位一到
# 0.7× 帧率就落在 13~14 之间，既不算慢也不算快，自适应会永远停在半清晰档上。
ADAPT_HIGH_FPS = 12.0

# 首帧档位：连上先用这一档把画面拍出来，再按实测帧率往上爬到用户的档位。
# 慢链路上 1.0×/q70 的第一帧接近 200 KB，光搬这一帧就要几秒，用户看到的是
# 「连上了但没画面」；0.5×/q60 大约只有它的四分之一。
START_SCALE = 0.5
START_QUALITY = 60

# 主控端断线重连：会话不是用户主动断开时，隔一小会儿自动重连，失败则退避加倍。
# 被控端重启只要几秒，所以首次间隔取小值；退避上限防止长时间空转。
VIEWER_RECONNECT_DELAY = 2.0
VIEWER_RECONNECT_MAX_DELAY = 20.0

# 被控端/中继还没把上一个会话收尾时会回 agent_busy。等它几秒通常就释放了，
# 直接报错等于逼用户再点一次「连接」，所以这里自己重试几次。
VIEWER_BUSY_RETRY_DELAY = 3.0
VIEWER_BUSY_RETRIES = 3


def human_bytes(size: int) -> str:
    """人类可读的体积；文件传输状态栏用得到。"""
    value = float(max(0, int(size)))
    for unit in ("B", "KB", "MB", "GB"):
        if unit == "B":
            if value < 1024:
                return f"{int(value)} B"
            continue
        value /= 1024.0
        if value < 1024 or unit == "GB":
            return f"{value:.1f} {unit}"
    return f"{value:.1f} GB"


def progress_text(direction: str, name: str, done: int, total: int) -> str:
    """「上传 notes.txt 42%（430 KB / 1.0 MB）」——进度、体积、总量一眼看到。"""
    total = max(0, int(total))
    done = max(0, int(done))
    percent = 100 if total == 0 else min(100, int(done * 100 / total))
    verb = "下载" if direction == "down" else "上传"
    return f"{verb} {name} {percent}%（{human_bytes(done)} / {human_bytes(total)}）"


def next_video_profile(profile: dict, fps: float, target: dict | None = None) -> dict | None:
    """AweSun-style auto quality: trade sharpness for frame rate on a slow link.

    ``target`` is the ceiling the climb must stop at — the profile the user actually asked
    for. Without it the climb only stops at the global caps.
    """
    scale = float(profile.get("scale", 1.0))
    quality = int(profile.get("quality", 70))
    if fps < ADAPT_LOW_FPS and scale > ADAPT_MIN_SCALE:
        return {
            **profile,
            "scale": max(ADAPT_MIN_SCALE, round(scale - 0.15, 3)),
            "quality": max(ADAPT_MIN_QUALITY, quality - 8),
        }
    ceiling_scale = min(1.0, float((target or {}).get("scale", 1.0)))
    ceiling_quality = min(ADAPT_MAX_QUALITY, int((target or {}).get("quality", ADAPT_MAX_QUALITY)))
    if fps > ADAPT_HIGH_FPS and (scale < ceiling_scale or quality < ceiling_quality):
        # 缩放先到顶，清晰度接着往上爬：scale 卡在 1.0 不是终点，用户要的 q 还没到。
        return {
            **profile,
            "scale": min(ceiling_scale, round(scale + 0.1, 3)),
            "quality": min(ceiling_quality, quality + 5),
            "fps": float((target or {}).get("fps", profile.get("fps", 15))),
        }
    return None


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
        self._last_remote_path = ""
        self.download_dir = DEFAULT_DOWNLOAD_DIR
        # 用户实际要的档位（滑块那一套）。自适应关掉时它就是生效档；开着时只是爬坡的终点。
        self._target: dict[str, float] = {"quality": 70, "scale": 1.0, "fps": 15}
        self._auto_quality = bool(ViewerConfig.load().auto_quality)
        # 生效档位。自适应开着时从 START_* 起步，再按实测帧率往 _target 爬。
        self._video: dict[str, float] = self._start_profile()
        self._frames_window = 0
        # Reconnect bookkeeping: remember the last session we opened, and whether the
        # close was the user's own doing (断开) or a fault the viewer should heal itself.
        self._reconnect_agent = ""
        self._reconnect_code = ""
        self._manual_disconnect = False
        self._reconnect_task: asyncio.Task | None = None

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
        adapt_task = asyncio.create_task(self._adapt_loop())
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
            adapt_task.cancel()
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
                await self._connect(payload)
            elif action == "disconnect":
                self._manual_disconnect = True
                self._cancel_reconnect()
                self._reconnect_agent = ""
                await self._teardown()
            elif action == "input" and self._session is not None:
                await self._session.send_text(payload)
            elif action == "frame_recv" and self._session is not None:
                await self._session.send_text({"t": "frame_recv"})
            elif action == "settings":
                self._remember_settings(payload)
                self._emit("auto_quality", self._auto_quality)
                if self._session is not None:
                    await self._session.send_text({"t": "set_quality", **self._video})
            elif action == "target":
                # 握手用：客户端只是告诉主控端「我要的档位是什么」，生效档仍停在首帧低档上。
                self._remember_target(payload)
                if self._session is not None:
                    await self._session.send_text({"t": "set_quality", **self._video})
            elif action == "auto_quality":
                self._auto_quality = bool(payload)
                if self._auto_quality:
                    # 打开自适应就从低档重新起步，别让已经拉满的档位把慢链路堵住。
                    self._video = self._start_profile()
                elif self._session is not None:
                    self._video = dict(self._target)
                if self._session is not None:
                    await self._session.send_text({"t": "set_quality", **self._video})
                self._emit("auto_quality", self._auto_quality)
            elif action == "upload" and self._session is not None:
                path = payload.get("path", "") if isinstance(payload, dict) else payload
                remote_dir = str(payload.get("dir", "")) if isinstance(payload, dict) else ""
                await self._upload(str(path), remote_dir)
            elif action == "download" and self._session is not None:
                path = payload.get("path", "") if isinstance(payload, dict) else payload
                await self._request_download(str(path))
            elif action == "list" and self._session is not None:
                path = payload.get("path", "") if isinstance(payload, dict) else payload
                await self._request_list(str(path))
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

    # ------------------------------------------------------------- picture profile
    def _start_profile(self) -> dict:
        """连上时先下发的档位。

        自适应开着就先用低档把首帧拍出来（慢链路上这决定了「秒出画面」还是「黑屏几秒」），
        自适应关着则是用户自己选的档位，不再替他做主。
        """
        if not self._auto_quality:
            return dict(self._target)
        return {
            "quality": min(int(self._target["quality"]), START_QUALITY),
            "scale": min(float(self._target["scale"]), START_SCALE),
            "fps": int(self._target["fps"]),
        }

    def _remember_target(self, payload) -> None:
        """只记目标档，不动当前生效档——握手时用它，避免把已经降下来的首帧又顶回满档。"""
        if not isinstance(payload, dict):
            return
        for key in ("quality", "scale", "fps"):
            if key in payload:
                self._target[key] = payload[key]

    def _remember_settings(self, payload) -> None:
        if not isinstance(payload, dict):
            return
        self._remember_target(payload)
        # A manual choice wins: stop the auto-quality loop from overriding it.
        self._auto_quality = False
        self._video = dict(self._target)

    async def _adapt_loop(self) -> None:
        """Watch the delivered frame rate and shrink the picture when the link is slow."""
        try:
            while True:
                await asyncio.sleep(ADAPT_WINDOW)
                if not self._auto_quality or self._session is None:
                    self._frames_window = 0
                    continue
                fps = self._frames_window / ADAPT_WINDOW
                self._frames_window = 0
                updated = next_video_profile(self._video, fps, self._target)
                if updated is None:
                    continue
                self._video = updated
                await self._session.send_text({"t": "set_quality", **updated})
                self._emit(
                    "log",
                    f"自适应画质：{updated['scale']:.2f}× / 质量 {updated['quality']}（实测 {fps:.1f} fps）",
                )
                self._emit("auto_profile", {"fps": fps, "scale": updated["scale"],
                                            "quality": updated["quality"]})
        except asyncio.CancelledError:
            pass

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

    async def _connect(self, payload) -> None:
        if self._client is None:
            self._emit("error", "请先登录")
            return
        if isinstance(payload, dict):
            agent_id = str(payload.get("agent_id", ""))
            code = str(payload.get("code", ""))
        else:
            agent_id, code = str(payload), ""
        if not agent_id:
            self._emit("error", "缺少设备 ID")
            return
        self._cancel_reconnect()
        self._manual_disconnect = False
        self._reconnect_agent = agent_id
        self._reconnect_code = code
        await self._teardown()
        self._emit("status", f"正在连接 {agent_id}…")
        session = await self._open_session(agent_id, code)
        self._session = session
        self._pairer = BinaryPairer()
        self._incoming = {}
        self._connected_agent = agent_id
        self._frames_window = 0
        # 先用首帧低档把画面拍出来，后面的升档交给自适应循环。
        self._video = self._start_profile()
        await session.send_text({"t": "set_quality", **self._video})
        self._emit("session_open", agent_id)
        asyncio.create_task(self._receive_loop(session))

    async def _open_session(self, agent_id: str, code: str):
        """开会话；撞上 agent_busy 时自动等被控端收尾再试。

        只重试 agent_busy：口令错、被控端离线、对方拒绝协助之类要立刻让用户看到。
        """
        delay = VIEWER_BUSY_RETRY_DELAY
        attempt = 0
        while True:
            try:
                return await self._client.open_session(agent_id, code)
            except DeskClientError as exc:
                if getattr(exc, "code", "") != "agent_busy" or attempt >= VIEWER_BUSY_RETRIES:
                    raise
                attempt += 1
                self._emit(
                    "status",
                    f"被控端还在结束上一个会话，{delay:.0f} 秒后重试（第 {attempt} 次）",
                )
                await asyncio.sleep(delay)

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
            if self._should_reconnect():
                self._schedule_reconnect()

    # ------------------------------------------------------------ reconnect
    def _should_reconnect(self) -> bool:
        """Self-heal a dropped session unless the user closed it on purpose."""
        return not self._stopping and not self._manual_disconnect and bool(self._reconnect_agent)

    def _cancel_reconnect(self) -> None:
        task = self._reconnect_task
        self._reconnect_task = None
        # Never cancel the coroutine that is calling us: a successful reconnect happens
        # inside the reconnect loop itself.
        if task is not None and not task.done() and task is not asyncio.current_task():
            task.cancel()

    def _schedule_reconnect(self) -> None:
        if self._reconnect_task is not None and not self._reconnect_task.done():
            return
        if self._loop is None:
            return
        self._reconnect_task = self._loop.create_task(self._reconnect_loop())

    async def _reconnect_loop(self) -> None:
        delay = VIEWER_RECONNECT_DELAY
        attempt = 0
        try:
            while self._should_reconnect():
                attempt += 1
                self._emit("status", f"连接已断开，{delay:.0f} 秒后重连（第 {attempt} 次）")
                self._emit("log", f"连接已断开，{delay:.0f} 秒后自动重连")
                await asyncio.sleep(delay)
                if not self._should_reconnect():
                    return
                agent_id = self._reconnect_agent
                try:
                    await self._connect({"agent_id": agent_id, "code": self._reconnect_code})
                except DeskClientError as exc:
                    self._emit("log", f"重连未成功：{exc}")
                    delay = min(delay * 1.5, VIEWER_RECONNECT_MAX_DELAY)
                    continue
                self._emit("log", "已自动重连，画面恢复")
                return
        except asyncio.CancelledError:
            pass
        finally:
            if self._reconnect_task is asyncio.current_task():
                self._reconnect_task = None

    async def _handle_text(self, message: dict) -> None:
        kind = message.get("t")
        if kind == "bin":
            self._pairer.header(message)
            return
        if kind == "meta":
            if message.get("kind") == "file_list":
                self._emit("remote_list", message)
                return
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
            self._frames_window += 1
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
            self._emit(
                "progress",
                {
                    "direction": "down",
                    "name": incoming.name,
                    "done": incoming.received,
                    "total": incoming.expected_size,
                },
            )
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

    async def _upload(self, path: str, remote_dir: str = "") -> None:
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
        offer = outgoing.offer()
        target = str(remote_dir or "").strip()
        if target:
            # 主控端选了远端目录：让被控端落到那个目录，而不是默认的 download_dir。
            offer["dir"] = target
        await session.send_text(offer)
        name = os.path.basename(path)
        sent = 0
        for header, chunk in outgoing.frames():
            await session.send_bin(header, chunk)
            sent += len(chunk)
            self._emit("progress", {"direction": "up", "name": name, "done": sent, "total": outgoing.size})
        where = f" → {target}" if target else ""
        self._emit("log", f"已上传：{name}{where}")

    async def _request_download(self, remote_path: str) -> None:
        """Ask the agent for a file; the agent answers with a direction="down" offer."""
        session = self._session
        if session is None:
            return
        path = str(remote_path or "").strip().strip('"')
        if not path:
            self._emit("error", "远程路径为空")
            return
        self._last_remote_path = path
        # 线上中继不转发未知类型，所以请求借 file_offer 承载，direction="get"。
        await session.send_text({"t": "file_offer", "direction": "get", "path": path})
        self._emit("log", f"已请求下载 {os.path.basename(path)}")

    async def _request_list(self, remote_path: str) -> None:
        """Ask the agent to list a directory; the agent answers with meta/file_list."""
        session = self._session
        if session is None:
            return
        path = str(remote_path or "").strip().strip('"')
        self._last_remote_path = path or self._last_remote_path
        await session.send_text({"t": "file_offer", "direction": "list", "path": path})

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
        # 远程文件窗口：打开着的对话框对象，以及上次访问的远端 / 本机目录
        self.remote_dialog = None
        self.last_remote_dir = ""
        self.last_local_dir = ""

        self.root = tk.Tk()
        self.root.title(f"{APP_NAME} 主控端 · v{app_version()}")
        self.root.geometry("1180x760")
        self.root.minsize(900, 600)
        # 托盘：关窗口只是收起，真要退出走托盘菜单或正在退出标记
        self.tray: TrayController | None = None
        self._quitting = False
        self._tray_hinted = False
        self._update_checked = False

        self.settings = ViewerConfig.load()
        self.url_var = tk.StringVar(value=self.settings.relay_url)
        self.password_var = tk.StringVar(value=self.settings.password)
        self.auto_var = tk.BooleanVar(value=bool(self.settings.auto_quality))
        self.adaptive_note = tk.StringVar(value="")
        self.code_var = tk.StringVar()
        self.quality_var = tk.IntVar(value=70)
        self.scale_var = tk.DoubleVar(value=1.0)
        self.fps_var = tk.IntVar(value=15)
        self.clip_var = tk.BooleanVar(value=False)
        self.status_var = tk.StringVar(value="未登录")
        self.metric_var = tk.StringVar(value="")
        self.login_info = tk.StringVar(value="")

        self._build_layout()
        self._setup_tray()
        self.root.after(30, self._drain_events)
        self.root.protocol("WM_DELETE_WINDOW", self._on_close)

    # ---------------------------------------------------------------- tray / update
    def _setup_tray(self) -> None:
        """右下角托盘图标；没有托盘后端就退化成普通窗口，并说明原因。"""
        try:
            tray = TrayController(
                APP_NAME,
                on_show=lambda: self.root.after(0, self._restore_window),
                on_quit=lambda: self.root.after(0, self._quit_from_tray),
                tooltip=f"{APP_NAME} v{app_version()}",
            )
            tray.start()
        except TrayUnavailable as exc:
            self.update_var.set(f"版本 v{app_version()} · 托盘不可用：{exc}")
            return
        except Exception as exc:  # 托盘后端偶尔在无桌面会话里直接抛
            self.update_var.set(f"版本 v{app_version()} · 托盘不可用：{exc!r}")
            return
        self.tray = tray

    def _restore_window(self) -> None:
        try:
            self.root.deiconify()
            self.root.lift()
            self.root.focus_force()
        except self._tk.TclError:
            pass

    def _quit_from_tray(self) -> None:
        self._quitting = True
        self._on_close()

    def _start_update_check(self, force: bool = False) -> None:
        if self._update_checked and not force:
            return
        self._update_checked = True
        self.update_var.set(f"版本 v{app_version()} · 正在检查更新…")
        current = app_version()

        def work() -> None:
            info = check_update(current)
            # 结果一律回 Tk 线程再动界面
            self.events.put(("update", info) if info else ("update_none", None))

        threading.Thread(target=work, name="ethandesk-update", daemon=True).start()

    def _show_update(self, info: dict) -> None:
        version = str(info.get("version", ""))
        current = str(info.get("current", app_version()))
        self.update_var.set(f"发现新版本 v{version}（当前 v{current}）")
        self.status_var.set(f"有新版本：v{version}")
        if self.tray is not None:
            self.tray.notify(f"发现新版本 v{version}，点托盘图标可查看", APP_NAME)
        if self._messagebox.askyesno(
            "发现新版本",
            f"EthanDesk 有新版本 v{version}（当前 v{current}）。\n\n是否打开下载页？",
        ):
            webbrowser.open(str(info.get("url", "")))

    # ---------------------------------------------------------------- layout
    def _build_layout(self) -> None:
        from tkinter import ttk

        from ui.theme import PALETTE, VIEWER_PAGES, apply_theme

        tk = self._tk
        apply_theme(self.root)
        self.root.configure(background=PALETTE["bg"])

        shell = ttk.Frame(self.root)
        shell.pack(fill="both", expand=True)

        side = ttk.Frame(shell, style="Sidebar.TFrame", width=188)
        side.pack(side="left", fill="y")
        side.pack_propagate(False)
        ttk.Label(side, text="EthanDesk", style="Sidebar.TLabel",
                  font=("Microsoft YaHei UI", 12, "bold")).pack(anchor="w", padx=18, pady=(20, 0))
        ttk.Label(side, text="主控端 · Free", style="SidebarMuted.TLabel").pack(anchor="w", padx=18, pady=(2, 22))

        self._nav_buttons = {}
        for name in VIEWER_PAGES:
            button = ttk.Button(side, text=name, style="Nav.TButton",
                                command=lambda n=name: self._show_page(n))
            button.pack(fill="x", padx=8, pady=1)
            self._nav_buttons[name] = button

        foot = ttk.Frame(side, style="Sidebar.TFrame")
        foot.pack(side="bottom", fill="x", padx=18, pady=16)
        ttk.Label(foot, textvariable=self.status_var, style="SidebarMuted.TLabel",
                  wraplength=150, justify="left").pack(anchor="w")
        self.update_var = tk.StringVar(value=f"版本 v{app_version()}")
        ttk.Label(foot, textvariable=self.update_var, style="SidebarMuted.TLabel",
                  wraplength=150, justify="left").pack(anchor="w", pady=(6, 0))
        ttk.Button(foot, text="检查更新", command=lambda: self._start_update_check(force=True)).pack(
            anchor="w", pady=(6, 0))

        main = ttk.Frame(shell)
        main.pack(side="left", fill="both", expand=True)

        top = ttk.Frame(main)
        top.pack(fill="x", padx=22, pady=(16, 4))
        self.search_var = tk.StringVar()
        ttk.Entry(top, textvariable=self.search_var, width=44).pack(side="left")
        ttk.Button(top, text="登录中继", style="Accent.TButton", command=self._login).pack(side="right")
        ttk.Label(top, textvariable=self.login_info, style="Muted.TLabel").pack(side="right", padx=12)

        body = ttk.Frame(main)
        body.pack(fill="both", expand=True, padx=22, pady=(4, 18))

        self.pages = {}
        self.pages["远程协助"] = self._page_assist(body)
        self.pages["设备"] = self._page_devices(body)
        self.pages["会话"] = self._page_session(body)
        self.pages["设置"] = self._page_settings(body)
        self._show_page("远程协助")

    # ------------------------------------------------------------- page router
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
        if name == "设备":
            self._refresh_cards()
        if name == "会话":
            self.canvas.focus_set()

    def _page_assist(self, parent):
        from tkinter import ttk

        page = ttk.Frame(parent)
        ttk.Label(page, text="远程协助", style="H1.TLabel").pack(anchor="w", pady=(4, 2))
        ttk.Label(page, text="输入伙伴识别码与验证码发起协助，或直接选中下方在线设备",
                  style="Muted.TLabel").pack(anchor="w")

        row = ttk.Frame(page)
        row.pack(fill="x", pady=(16, 10))
        self.target_var = self._tk.StringVar()
        ttk.Entry(row, textvariable=self.target_var, width=26).pack(side="left")
        ttk.Label(row, text="验证码", style="Muted.TLabel").pack(side="left", padx=(12, 6))
        ttk.Entry(row, textvariable=self.code_var, width=14).pack(side="left")
        ttk.Button(row, text="连接", style="Accent.TButton", width=12,
                   command=self._connect_target).pack(side="left", padx=12)

        ttk.Separator(page).pack(fill="x", pady=(8, 12))
        ttk.Label(page, text="在线设备", style="Section.TLabel").pack(anchor="w", pady=(0, 6))
        self.tree = ttk.Treeview(page, columns=("name", "host", "status"), show="headings",
                                 height=10, selectmode="browse")
        for column, label, width in (("name", "名称", 200), ("host", "主机", 220), ("status", "状态", 90)):
            self.tree.heading(column, text=label)
            self.tree.column(column, width=width)
        self.tree.pack(fill="both", expand=True)
        self.tree.bind("<Double-1>", lambda _event: self._connect_selected())

        bar = ttk.Frame(page)
        bar.pack(fill="x", pady=(10, 0))
        ttk.Button(bar, text="刷新", command=lambda: self.commands.put(("refresh_agents", None))).pack(side="left")
        ttk.Button(bar, text="连接选中", style="Accent.TButton",
                   command=self._connect_selected).pack(side="left", padx=8)
        ttk.Button(bar, text="断开", command=self._disconnect).pack(side="left")
        return page

    def _page_devices(self, parent):
        from tkinter import ttk

        page = ttk.Frame(parent)
        ttk.Label(page, text="设备", style="H1.TLabel").pack(anchor="w", pady=(4, 2))
        ttk.Label(page, text="登录中继后，在线设备以卡片形式出现", style="Muted.TLabel").pack(anchor="w")
        self.cards = ttk.Frame(page)
        self.cards.pack(fill="both", expand=True, pady=(12, 8))

        ttk.Label(page, text="会话记录", style="Section.TLabel").pack(anchor="w", pady=(4, 6))
        self.history = ttk.Treeview(page, columns=("agent", "viewer", "start", "duration", "frames"),
                                    show="headings", height=5)
        for column, label, width in (
            ("agent", "设备", 160),
            ("viewer", "操作人", 110),
            ("start", "开始时间", 170),
            ("duration", "时长(秒)", 90),
            ("frames", "帧数", 80),
        ):
            self.history.heading(column, text=label)
            self.history.column(column, width=width)
        self.history.pack(fill="x")
        bar = ttk.Frame(page)
        bar.pack(fill="x", pady=(6, 0))
        ttk.Button(bar, text="刷新记录",
                   command=lambda: self.commands.put(("refresh_sessions", None))).pack(side="left")
        return page

    def _page_session(self, parent):
        from tkinter import ttk

        tk = self._tk
        page = ttk.Frame(parent)
        head = ttk.Frame(page)
        head.pack(fill="x")
        ttk.Label(head, text="会话", style="H1.TLabel").pack(side="left")
        ttk.Label(head, textvariable=self.metric_var, style="Muted.TLabel").pack(side="right")

        self.canvas = tk.Canvas(page, background="#0B0C0E", highlightthickness=0, takefocus=1)
        self.canvas.pack(fill="both", expand=True, pady=10)
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

        bar = ttk.Frame(page)
        bar.pack(fill="x")
        ttk.Button(bar, text="断开", command=self._disconnect).pack(side="left")
        ttk.Button(bar, text="上传文件…", command=self._pick_upload).pack(side="left", padx=8)
        ttk.Button(bar, text="远程文件…", command=self._open_remote_files).pack(side="left")
        ttk.Checkbutton(bar, text="剪贴板同步", variable=self.clip_var,
                        command=self._toggle_clipboard).pack(side="left", padx=12)
        ttk.Label(bar, textvariable=self.status_var, style="Muted.TLabel").pack(side="right")
        return page

    def _page_settings(self, parent):
        from tkinter import ttk

        page = ttk.Frame(parent)
        ttk.Label(page, text="设置", style="H1.TLabel").pack(anchor="w", pady=(4, 10))
        split = ttk.Frame(page)
        split.pack(fill="both", expand=True)

        sub = ttk.Frame(split, width=140)
        sub.pack(side="left", fill="y")
        sub.pack_propagate(False)
        right = ttk.Frame(split)
        right.pack(side="left", fill="both", expand=True, padx=(18, 0))

        self._settings_panels = {}
        self._settings_buttons = {}
        panels = {
            "本机信息": self._panel_about(right),
            "网络": self._panel_network(right),
            "画面": self._panel_picture(right),
        }
        self._settings_panels = panels
        for name in panels:
            button = ttk.Button(sub, text=name, style="SubNav.TButton",
                                command=lambda n=name: self._show_settings_panel(n))
            button.pack(fill="x", pady=1)
            self._settings_buttons[name] = button
        self._show_settings_panel("网络")
        return page

    def _show_settings_panel(self, name: str) -> None:
        for key, frame in getattr(self, "_settings_panels", {}).items():
            if key == name:
                frame.pack(fill="both", expand=True)
            else:
                frame.pack_forget()
        for key, button in getattr(self, "_settings_buttons", {}).items():
            button.configure(style="SubNavOn.TButton" if key == name else "SubNav.TButton")

    def _panel_about(self, parent):
        from tkinter import ttk

        import socket

        frame = ttk.Frame(parent)
        rows = (
            ("设备名", socket.gethostname()),
            ("角色", "主控端"),
            ("版本", "1.0.1"),
        )
        for index, (label, value) in enumerate(rows):
            ttk.Label(frame, text=label, style="Muted.TLabel").grid(row=index, column=0, sticky="w", pady=6)
            ttk.Label(frame, text=value).grid(row=index, column=1, sticky="w", padx=18, pady=6)
        ttk.Label(frame, text="中继地址", style="Muted.TLabel").grid(row=3, column=0, sticky="w", pady=6)
        ttk.Label(frame, textvariable=self.url_var).grid(row=3, column=1, sticky="w", padx=18, pady=6)
        ttk.Label(frame, text="登录状态", style="Muted.TLabel").grid(row=4, column=0, sticky="w", pady=6)
        ttk.Label(frame, textvariable=self.login_info).grid(row=4, column=1, sticky="w", padx=18, pady=6)
        return frame

    def _panel_network(self, parent):
        from tkinter import ttk

        from common.defaults import DEFAULT_VIEWER_URL

        frame = ttk.Frame(parent)
        ttk.Label(frame, text="中继地址", style="Muted.TLabel").pack(anchor="w")
        ttk.Entry(frame, textvariable=self.url_var, width=46).pack(anchor="w", pady=(2, 2))
        ttk.Label(frame, text=f"默认：{DEFAULT_VIEWER_URL}", style="Muted.TLabel").pack(anchor="w")
        ttk.Label(frame, text="登录口令", style="Muted.TLabel").pack(anchor="w", pady=(14, 0))
        ttk.Entry(frame, textvariable=self.password_var, show="*", width=28).pack(anchor="w", pady=(2, 0))

        bar = ttk.Frame(frame)
        bar.pack(anchor="w", pady=(16, 0))

        def restore_default() -> None:
            self.url_var.set(DEFAULT_VIEWER_URL)

        ttk.Button(bar, text="恢复默认", command=restore_default).pack(side="left")
        ttk.Button(bar, text="保存并登录", style="Accent.TButton", command=self._login).pack(side="left", padx=8)
        return frame

    def _panel_picture(self, parent):
        from tkinter import ttk

        frame = ttk.Frame(parent)
        for label, widget in (("画质", "quality"), ("缩放", "scale"), ("帧率", "fps")):
            ttk.Label(frame, text=label, style="Muted.TLabel").pack(anchor="w", pady=(10, 0))
            if widget == "quality":
                ttk.Scale(frame, from_=20, to=95, variable=self.quality_var, orient="horizontal",
                          length=220, command=lambda _e: self._send_settings()).pack(anchor="w")
            elif widget == "scale":
                ttk.Scale(frame, from_=0.25, to=1.0, variable=self.scale_var, orient="horizontal",
                          length=220, command=lambda _e: self._send_settings()).pack(anchor="w")
            else:
                ttk.Spinbox(frame, from_=1, to=30, textvariable=self.fps_var, width=6,
                            command=self._send_settings).pack(anchor="w")
        ttk.Checkbutton(frame, text="自适应画质（慢链路自动降低清晰度保帧率）",
                        variable=self.auto_var,
                        command=self._toggle_auto).pack(anchor="w", pady=(12, 0))
        ttk.Label(frame, textvariable=self.adaptive_note, style="Muted.TLabel").pack(anchor="w")
        return frame

    def _toggle_auto(self) -> None:
        enabled = bool(self.auto_var.get())
        self.settings.auto_quality = enabled
        self._save_settings()
        self.commands.put(("auto_quality", enabled))
        if not enabled:
            self.adaptive_note.set("")

    # ---------------------------------------------------------------- actions
    def _connect_target(self) -> None:
        agent_id = self.target_var.get().strip()
        if not agent_id:
            self._connect_selected()
            return
        self.commands.put(("connect", {"agent_id": agent_id, "code": self.code_var.get().strip()}))
        self.status_var.set(f"正在连接 {agent_id}…")
        self._show_page("会话")

    def _connect_item(self, item: str) -> None:
        try:
            self.tree.selection_set(item)
        except Exception:
            pass
        self._connect_selected()
        self._show_page("会话")

    def _refresh_cards(self) -> None:
        from tkinter import ttk

        cards = getattr(self, "cards", None)
        tree = getattr(self, "tree", None)
        if cards is None or tree is None:
            return
        for child in cards.winfo_children():
            child.destroy()
        rows = list(tree.get_children())
        if not rows:
            ttk.Label(cards, text="暂无在线设备", style="Muted.TLabel").pack(anchor="w", pady=10)
            return
        columns = 3
        for index, item in enumerate(rows):
            values = tree.item(item, "values")
            name = values[0] if values else item
            host = values[1] if len(values) > 1 else ""
            card = ttk.Frame(cards, style="Card.TFrame", padding=14)
            card.grid(row=index // columns, column=index % columns, sticky="nsew", padx=6, pady=6)
            ttk.Label(card, text=str(name), style="Panel.TLabel",
                      font=("Microsoft YaHei UI", 11, "bold")).pack(anchor="w")
            ttk.Label(card, text=str(host), style="PanelMuted.TLabel").pack(anchor="w", pady=(2, 12))
            actions = ttk.Frame(card, style="Card.TFrame")
            actions.pack(anchor="w")
            ttk.Button(actions, text="桌面控制", style="Accent.TButton",
                       command=lambda i=item: self._connect_item(i)).pack(side="left")
            ttk.Button(actions, text="桌面观看",
                       command=lambda i=item: self._connect_item(i)).pack(side="left", padx=6)
        for column in range(columns):
            cards.grid_columnconfigure(column, weight=1)


    def _login(self) -> None:
        url = self.url_var.get().strip() or DEFAULT_VIEWER_URL
        password = self.password_var.get()
        self.settings.relay_url = url
        self.settings.password = password
        self._save_settings()
        if not url:
            self._messagebox.showerror("缺少地址", "请填写中继地址")
            return
        self.url_var.set(url)
        self.status_var.set("正在登录…")
        self.commands.put(("login", {"url": url, "password": password}))

    def _connect_selected(self) -> None:
        selection = self.tree.selection()
        if not selection:
            self._messagebox.showinfo("未选择设备", "请先在左侧选择一个在线设备")
            return
        agent_id = selection[0]
        self.commands.put(("connect", {"agent_id": agent_id, "code": self.code_var.get().strip()}))
        self.status_var.set("等待被控端确认…")

    def _save_settings(self) -> None:
        try:
            self.settings.save()
        except OSError as exc:
            self._messagebox.showwarning("保存失败", f"无法写入配置文件：{exc}")

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

    def _open_remote_files(self) -> None:
        existing = getattr(self, "remote_dialog", None)
        if existing is not None:
            try:
                existing.top.lift()
                existing.refresh()
                return
            except Exception:  # 窗口已被关掉但引用还在
                self.remote_dialog = None
        self.remote_dialog = RemoteFileDialog(self)

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
        # 可打印字符（含中文/日文/emoji）按字符注入：远端输入法正在组字、或者
        # CapsLock 开着，落下来的也还是主控端打的那个字符。Ctrl/Alt 组合键仍走
        # 虚拟键，否则复制粘贴就没有了。
        char = getattr(event, "char", "") or ""
        if down:
            text = text_for_key(char, int(getattr(event, "state", 0) or 0))
            if text:
                self.commands.put(("input", {"t": "input", "ev": "text", "text": text}))
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
                if self.auto_var.get():
                    self.commands.put(("auto_quality", True))
            elif kind == "auto_profile":
                fps = float(payload.get("fps", 0.0) or 0.0)
                self.adaptive_note.set(
                    f"自适应中：{payload.get('scale')}× / q{payload.get('quality')}（{fps:.1f} fps）"
                )
            elif kind == "auto_quality":
                self.adaptive_note.set("自适应画质已开启，等待链路测量…" if payload else "")
            elif kind == "clip":
                self.last_clip_text = str(payload)
                self._write_clipboard(str(payload))
            elif kind == "clip_poll":
                self._poll_clipboard()
            elif kind == "file_saved":
                if getattr(self, "remote_dialog", None) is not None:
                    self.remote_dialog.status_var.set(f"已保存到：{payload}")
                self._messagebox.showinfo("文件已接收", f"已保存到：{payload}")
            elif kind == "progress":
                dialog = getattr(self, "remote_dialog", None)
                if dialog is not None and isinstance(payload, dict):
                    dialog.status_var.set(
                        progress_text(
                            str(payload.get("direction", "")),
                            str(payload.get("name", "")),
                            int(payload.get("done", 0) or 0),
                            int(payload.get("total", 0) or 0),
                        )
                    )
            elif kind == "remote_list":
                dialog = getattr(self, "remote_dialog", None)
                if dialog is not None:
                    try:
                        dialog.render(payload)
                        if payload.get("path"):
                            self.last_remote_dir = str(payload.get("path"))
                    except Exception:  # 窗口刚被关掉
                        self.remote_dialog = None
            elif kind == "session_open":
                self.status_var.set(f"已连接到 {payload}")
            elif kind == "session_closed":
                self.status_var.set("会话已结束")
                self.frame_image = None
                self.canvas.delete("frame")
            elif kind == "update":
                if isinstance(payload, dict):
                    self._show_update(payload)
            elif kind == "update_none":
                self.update_var.set(f"版本 v{app_version()} · 已是最新")
            elif kind == "logged_in":
                self.login_info.set(f"已登录：{payload}")
                self._start_update_check()
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
        # 有托盘时关窗口只是收起，和向日葵一致：图标还在，双击就回来
        if self.tray is not None and not getattr(self, "_quitting", False):
            try:
                self.root.withdraw()
            except self._tk.TclError:
                pass
            if not getattr(self, "_tray_hinted", False):
                self._tray_hinted = True
                self.tray.notify("EthanDesk 还在后台运行，双击托盘图标可以恢复窗口", APP_NAME)
            return
        self.commands.put(("shutdown", None))
        if self.tray is not None:
            self.tray.stop()
            self.tray = None
        self.root.destroy()

    def run(self) -> None:
        self.root.mainloop()


class FilePane:
    """双栏窗口里的一侧：本机栏自己扫本地盘，远端栏等被控端回 meta/file_list。

    只负责显示与导航；真正发请求、收文件都交给 ``RemoteFileDialog`` 转给
    ``ViewerWorker``，这样这一层没有任何网络/线程逻辑，测试起来也简单。
    """

    def __init__(self, dialog: "RemoteFileDialog", parent, title: str, *, remote: bool,
                 initial: str, width: int = 26) -> None:
        import tkinter as tk

        self.dialog = dialog
        self.remote = remote
        self.frame = tk.Frame(parent)
        tk.Label(self.frame, text=title, anchor="w").pack(fill="x")

        bar = tk.Frame(self.frame)
        bar.pack(fill="x", pady=(4, 4))
        tk.Button(bar, text="上级", width=5, command=self.go_up).pack(side="left")
        self.path_var = tk.StringVar(value=initial or "")
        entry = tk.Entry(bar, textvariable=self.path_var)
        entry.pack(side="left", fill="x", expand=True, padx=4)
        entry.bind("<Return>", lambda _event: self.refresh())
        tk.Button(bar, text="刷新", width=5, command=self.refresh).pack(side="left")

        body = tk.Frame(self.frame)
        body.pack(fill="both", expand=True)
        self.listbox = tk.Listbox(body, activestyle="none", width=width, exportselection=False)
        self.listbox.pack(side="left", fill="both", expand=True)
        scroll = tk.Scrollbar(body, orient="vertical", command=self.listbox.yview)
        scroll.pack(side="right", fill="y")
        self.listbox.configure(yscrollcommand=scroll.set)
        self.listbox.bind("<Double-Button-1>", self._on_open)

        self.footer = tk.StringVar(value="")
        tk.Label(self.frame, textvariable=self.footer, anchor="w").pack(fill="x", pady=(4, 0))
        self.entries: list[dict] = []

    # ------------------------------------------------------------------ loading
    def refresh(self) -> None:
        if self.remote:
            self.dialog.request_remote(self.path_var.get())
        else:
            self.load_local(self.path_var.get())

    def load_local(self, path: str) -> None:
        from common.listing import list_directory

        entries, directory, error = list_directory(path, 500)
        self.show(entries, directory, error)

    def show(self, entries: list[dict], directory: str, error: str) -> None:
        if directory:
            self.path_var.set(directory)
        self.entries = list(entries)
        self.listbox.delete(0, "end")
        for entry in self.entries:
            name = str(entry.get("name", ""))
            if entry.get("dir"):
                label = f"[{name}]"
            else:
                label = f" {name}    {format_size(int(entry.get('size') or 0))}"
            self.listbox.insert("end", label)
        if error:
            self.footer.set(f"拒绝：{error}")
        else:
            cut = "（已截断）" if len(self.entries) >= 500 else ""
            self.footer.set(f"{directory or '顶层'} · {len(self.entries)} 项{cut}")

    # ------------------------------------------------------------------ actions
    def go_up(self) -> None:
        from common.listing import parent_of

        self.path_var.set(parent_of(self.path_var.get()))
        self.refresh()

    def _on_open(self, _event=None) -> None:
        entry = self.selected()
        if entry is None:
            return
        if entry.get("dir"):
            base = self.path_var.get().rstrip("\\/")
            name = str(entry.get("name", ""))
            self.path_var.set(f"{base}\\{name}" if base else name)
            self.refresh()
        else:
            self.dialog.activate(self)

    def selected(self) -> dict | None:
        selection = self.listbox.curselection()
        if not selection:
            return None
        index = selection[0]
        return self.entries[index] if 0 <= index < len(self.entries) else None

    def selected_path(self) -> str:
        entry = self.selected()
        if entry is None:
            return ""
        base = self.path_var.get().rstrip("\\/")
        name = str(entry.get("name", ""))
        return f"{base}\\{name}" if base else name

    def directory(self) -> str:
        return self.path_var.get().rstrip("\\/")


class RemoteFileDialog:
    """向日葵式远程文件窗口：左边本机、右边被控端，中间上传/下载。

    远端目录数据由 ``ViewerWorker`` 通过 ``remote_list`` 事件送回，窗口只往命令
    队列塞 ``("list", path)`` / ``("download", path)`` / ``("upload", {...})``。
    """

    def __init__(self, window: "ViewerWindow") -> None:
        import tkinter as tk

        self.window = window
        self.top = tk.Toplevel(window.root)
        self.top.title("远程文件")
        self.top.geometry("1080x560")
        self.top.minsize(820, 420)
        self.top.transient(window.root)

        body = tk.Frame(self.top)
        body.pack(fill="both", expand=True, padx=10, pady=(10, 4))

        local_start = window.last_local_dir or os.path.expanduser("~")
        self.local = FilePane(
            self, body, f"本机 · {os.environ.get('COMPUTERNAME', '这台电脑')}",
            remote=False, initial=local_start,
        )
        self.local.frame.pack(side="left", fill="both", expand=True)

        middle = tk.Frame(body)
        middle.pack(side="left", fill="y", padx=10)
        tk.Label(middle, text="").pack(pady=60)
        tk.Button(middle, text="上传 →", width=9, command=self.upload_selected).pack(pady=4)
        tk.Button(middle, text="← 下载", width=9, command=self.download_selected).pack(pady=4)

        self.remote_pane = FilePane(
            self, body, "远端 · 被控端", remote=True, initial=window.last_remote_dir or "",
        )
        self.remote_pane.frame.pack(side="left", fill="both", expand=True)

        self.status_var = tk.StringVar(value="正在请求远端目录…")
        foot = tk.Frame(self.top)
        foot.pack(fill="x", padx=10, pady=(6, 10))
        tk.Label(foot, textvariable=self.status_var, anchor="w").pack(side="left", fill="x", expand=True)
        tk.Button(foot, text="关闭", command=self.close).pack(side="right")

        self.local.refresh()
        self.remote_pane.refresh()
        self.top.protocol("WM_DELETE_WINDOW", self.close)

    # ------------------------------------------------------------------ bridge
    def request_remote(self, path: str) -> None:
        self.status_var.set("正在请求远端目录…")
        self.window.commands.put(("list", str(path or "")))

    def refresh(self) -> None:
        self.local.refresh()
        self.remote_pane.refresh()

    def render(self, payload: dict) -> None:
        error = str(payload.get("error") or "")
        entries = list(payload.get("entries") or [])
        directory = str(payload.get("path") or "")
        self.remote_pane.show(entries, directory, error)
        if directory:
            self.window.last_remote_dir = directory
            self.status_var.set(f"远端：{directory} · {len(entries)} 项")
        elif error:
            self.status_var.set(f"被控端拒绝：{error}")
        else:
            self.status_var.set("远端：顶层（选择磁盘）")

    def activate(self, pane: FilePane) -> None:
        if pane is self.local:
            self.upload_selected()
        else:
            self.download_selected()

    def upload_selected(self) -> None:
        entry = self.local.selected()
        if entry is None:
            self.status_var.set("先在左边选一个本机文件")
            return
        if entry.get("dir"):
            self.status_var.set("选中的是本机目录，双击可以进去")
            return
        local_path = self.local.selected_path()
        remote_dir = self.remote_pane.directory()
        if not remote_dir:
            self.status_var.set("右边先选一个远端目录（点 [C:\\] 进去）")
            return
        self.status_var.set(f"正在上传 {entry.get('name')} → {remote_dir}")
        self.window.commands.put(("upload", {"path": local_path, "dir": remote_dir}))

    def download_selected(self) -> None:
        entry = self.remote_pane.selected()
        if entry is None:
            self.status_var.set("先在右边选一个远端文件")
            return
        if entry.get("dir"):
            self.status_var.set("选中的是远端目录，双击可以进去")
            return
        remote_path = self.remote_pane.selected_path()
        self.status_var.set(f"正在下载 {remote_path}")
        self.window.commands.put(("download", remote_path))

    def close(self) -> None:
        self.window.last_remote_dir = self.remote_pane.directory()
        self.window.last_local_dir = self.local.directory()
        if getattr(self.window, "remote_dialog", None) is self:
            self.window.remote_dialog = None
        self.top.destroy()


def format_size(size: int) -> str:
    value = float(max(0, size))
    for unit in ("B", "KB", "MB", "GB"):
        if value < 1024 or unit == "GB":
            return f"{value:.0f} {unit}" if unit == "B" else f"{value:.1f} {unit}"
        value /= 1024
    return f"{size} B"


def main() -> None:
    ViewerWindow().run()


if __name__ == "__main__":
    main()
