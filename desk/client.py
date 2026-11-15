"""Unified AweSun-style client: 被控端 + 主控端 in a single window.

Layout mirrors the AweSun desktop client: a dark sidebar with page navigation, a
top search bar, a 远程协助 page that shows this machine's identification code next
to the connect-to-a-peer row, a 设备 page with a desktop preview, and a 设置 page
with a secondary navigation column.

Both engines already exist as workers: :class:`agent.app.AgentWorker` owns the
incoming side, :class:`viewer.app.ViewerWorker` owns the outgoing side. This
module only wires them into one shell.
"""

from __future__ import annotations

import queue
import socket
import time

PAGES = ("远程协助", "设备", "会话", "设置")
SETTINGS_PAGES = ("本机信息", "常规", "安全", "网络", "画面")

POLICY_CHOICES = (("永不", "never"), ("每日", "daily"), ("每次被控后", "every_session"))


def mask_code(code: str) -> str:
    """Return the dotted placeholder AweSun shows before the code is revealed."""
    digits = "".join(ch for ch in str(code) if ch.isdigit())
    if not digits:
        return ""
    head = "•" * min(3, len(digits))
    return f"{head} •••"


def connect_label(connected: bool) -> str:
    return "断开" if connected else "连接"


class DeskWindow:
    def __init__(self, start_threads: bool = True) -> None:
        import tkinter as tk
        from tkinter import messagebox, ttk

        self._tk = tk
        self._ttk = ttk
        self._messagebox = messagebox

        from agent.app import AgentWorker
        from agent.config import AgentConfig
        from viewer.app import ViewerWorker
        from viewer.config import ViewerConfig

        self.agent_config = AgentConfig.load()
        self.viewer_config = ViewerConfig.load()

        self.agent_events: "queue.Queue" = queue.Queue()
        self.agent_commands: "queue.Queue" = queue.Queue()
        self.viewer_events: "queue.Queue" = queue.Queue()
        self.viewer_commands: "queue.Queue" = queue.Queue()

        self.agent_worker = AgentWorker(self.agent_config, self.agent_events, self.agent_commands)
        self.viewer_worker = ViewerWorker(self.viewer_events, self.viewer_commands)
        if start_threads:
            self.viewer_worker.start()

        self.online_agents: list = []
        self.frame_image = None
        self.frame_size = (0, 0)
        self._photo = None
        self.fps_counter = 0
        self.fps_window_started = time.time()
        self.current_fps = 0.0
        self.clipboard_enabled = False
        self.last_clip_text = ""
        self.connected = False
        self.agent_running = False
        self.code_revealed = False

        from common.dpi import ensure_dpi_aware, tk_scaling

        ensure_dpi_aware()
        self.root = tk.Tk()
        tk_scaling(self.root)
        self.root.title("EthanDesk 远程桌面")
        self.root.geometry("1280x820")
        self.root.minsize(1080, 700)

        from ui.theme import format_ident

        self._format_ident = format_ident
        self.ident_var = tk.StringVar(value=format_ident(self.agent_config.agent_id))
        self.code_var = tk.StringVar(value=self.agent_config.session_code)
        self.code_view = tk.StringVar(value=mask_code(self.agent_config.session_code))
        self.device_var = tk.StringVar(value=self.agent_config.name or socket.gethostname())
        self.note_var = tk.StringVar(value="")
        self.agent_url_var = tk.StringVar(value=self.agent_config.relay_url)
        self.viewer_url_var = tk.StringVar(value=self.viewer_config.relay_url)
        self.password_var = tk.StringVar(value=self.viewer_config.password)
        self.key_var = tk.StringVar(value=self.agent_config.agent_key)
        self.allow_var = tk.BooleanVar(value=bool(self.agent_config.allow_input))
        self.clip_var = tk.BooleanVar(value=bool(self.agent_config.clipboard_sync))
        self.unattended_var = tk.BooleanVar(value=bool(self.agent_config.unattended))
        self.policy_var = tk.StringVar(value=self._policy_label(self.agent_config.session_code_policy))
        self.peer_var = tk.StringVar()
        self.peer_code_var = tk.StringVar()
        self.quality_var = tk.IntVar(value=int(self.agent_config.quality))
        self.scale_var = tk.DoubleVar(value=float(self.agent_config.scale))
        self.fps_var = tk.IntVar(value=int(self.agent_config.fps))
        self.auto_var = tk.BooleanVar(value=bool(self.viewer_config.auto_quality))
        self.adaptive_note = tk.StringVar(value="")
        self.status_var = tk.StringVar(value="未启动")
        self.serve_var = tk.BooleanVar(value=False)
        self._relay_state = "connecting"
        self.serve_status = tk.StringVar(value="已关闭")
        self.metric_var = tk.StringVar(value="")
        self.login_info = tk.StringVar(value="未登录")

        self._build_layout()
        self.root.after(60, self._drain)
        self.root.protocol("WM_DELETE_WINDOW", self._on_close)
        if start_threads and self.agent_config.serve_on_start:
            # 和向日葵一样：装好就能被协助，不需要再点一次“开始服务”。
            self.serve_var.set(True)
            self._start_agent()
        if start_threads and self.viewer_config.password:
            # 存过口令就自动登录中继，首页不该停在“未登录”让人再点一次。
            self.root.after(300, self._login)

    # ---------------------------------------------------------------- helpers
    @staticmethod
    def _policy_label(policy: str) -> str:
        for label, value in POLICY_CHOICES:
            if value == policy:
                return label
        return "每次被控后"

    def _policy_value(self) -> str:
        label = self.policy_var.get()
        for name, value in POLICY_CHOICES:
            if name == label:
                return value
        return "every_session"

    # ---------------------------------------------------------------- layout
    def _build_layout(self) -> None:
        tk = self._tk
        ttk = self._ttk
        from ui.theme import PALETTE, apply_theme

        apply_theme(self.root)
        self.root.configure(background=PALETTE["bg"])

        shell = ttk.Frame(self.root)
        shell.pack(fill="both", expand=True)
        self._sidebar(shell)

        main = ttk.Frame(shell)
        main.pack(side="left", fill="both", expand=True)
        self._topbar(main)

        body = ttk.Frame(main)
        body.pack(fill="both", expand=True, padx=26, pady=(6, 20))
        self.pages = {
            "远程协助": self._page_assist(body),
            "设备": self._page_devices(body),
            "会话": self._page_session(body),
            "设置": self._page_settings(body),
        }
        self._show_page("远程协助")

    def _sidebar(self, parent) -> None:
        tk = self._tk
        ttk = self._ttk
        from ui.theme import PALETTE, PAGES as _unused  # noqa: F401

        side = ttk.Frame(parent, style="Sidebar.TFrame", width=196)
        side.pack(side="left", fill="y")
        side.pack_propagate(False)

        head = ttk.Frame(side, style="Sidebar.TFrame")
        head.pack(fill="x", padx=16, pady=(18, 10))
        avatar = tk.Canvas(head, width=38, height=38, background=PALETTE["sidebar"],
                           highlightthickness=0)
        avatar.pack(side="left")
        avatar.create_oval(2, 2, 36, 36, fill=PALETTE["accent"], outline="")
        avatar.create_text(19, 19, text="E", fill="#FFFFFF", font=("Arial", 15, "bold"))
        names = ttk.Frame(head, style="Sidebar.TFrame")
        names.pack(side="left", padx=10)
        ttk.Label(names, text="EthanDesk", style="Sidebar.TLabel",
                  font=("Microsoft YaHei UI", 10, "bold")).pack(anchor="w")
        ttk.Label(names, text="Free", style="SidebarMuted.TLabel").pack(anchor="w")

        self._nav_buttons = {}
        nav = ttk.Frame(side, style="Sidebar.TFrame")
        nav.pack(fill="x", pady=(6, 0))
        for name in PAGES:
            button = ttk.Button(nav, text=name, style="Nav.TButton",
                                command=lambda n=name: self._show_page(n))
            button.pack(fill="x", padx=10, pady=1)
            self._nav_buttons[name] = button

        foot = ttk.Frame(side, style="Sidebar.TFrame")
        foot.pack(side="bottom", fill="x", padx=10, pady=(0, 14))
        ttk.Button(foot, text="设置", style="Nav.TButton",
                   command=lambda: self._show_page("设置")).pack(fill="x")
        ttk.Label(side, textvariable=self.status_var, style="SidebarMuted.TLabel",
                  wraplength=160, justify="left").pack(side="bottom", anchor="w", padx=18, pady=8)

    def _topbar(self, parent) -> None:
        tk = self._tk
        ttk = self._ttk
        from ui.theme import PALETTE

        top = ttk.Frame(parent)
        top.pack(fill="x", padx=26, pady=(16, 0))
        left = ttk.Frame(top)
        left.pack(side="left", fill="x", expand=True)
        self.search_var = tk.StringVar()
        ttk.Entry(left, textvariable=self.search_var, width=52).pack(anchor="center", pady=2)
        right = ttk.Frame(top)
        right.pack(side="right")
        ttk.Label(right, textvariable=self.login_info, style="Muted.TLabel").pack(side="left", padx=8)
        ttk.Button(right, text="登录中继", style="Accent.TButton", command=self._login).pack(side="left")

    # ------------------------------------------------------------- page builds
    def _page_assist(self, parent):
        tk = self._tk
        ttk = self._ttk

        page = ttk.Frame(parent)

        header = ttk.Frame(page)
        header.pack(fill="x", pady=(6, 0))
        ttk.Label(header, text="远程协助本机", style="H1.TLabel").pack(side="left")
        self.serve_toggle = ttk.Checkbutton(header, text="允许他人协助本机",
                                            variable=self.serve_var, command=self._toggle_serve)
        self.serve_toggle.pack(side="left", padx=14)
        ttk.Label(header, textvariable=self.serve_status, style="Muted.TLabel").pack(side="left")
        ttk.Label(page, text="打开后本机会自动在中继上线；只有中继确认在线才会显示“可接收协助”。",
                  style="Muted.TLabel").pack(anchor="w", pady=(4, 0))

        grid = ttk.Frame(page)
        grid.pack(fill="x", pady=(18, 6))
        ident_cell = ttk.Frame(grid)
        ident_cell.pack(side="left", anchor="n")
        ttk.Label(ident_cell, text="本机识别码", style="Muted.TLabel").pack(anchor="w")
        ident_row = ttk.Frame(ident_cell)
        ident_row.pack(anchor="w")
        ttk.Label(ident_row, textvariable=self.ident_var, style="Ident.TLabel").pack(side="left")
        ttk.Button(ident_row, text="复制", style="Ghost.TButton",
                   command=self._copy_ident).pack(side="left", padx=10)

        code_cell = ttk.Frame(grid)
        code_cell.pack(side="left", anchor="n", padx=(60, 0))
        ttk.Label(code_cell, text="单次验证码", style="Muted.TLabel").pack(anchor="w")
        code_row = ttk.Frame(code_cell)
        code_row.pack(anchor="w")
        ttk.Label(code_row, textvariable=self.code_view, style="Code.TLabel").pack(side="left")
        self.eye_button = ttk.Button(code_row, text="显示", style="Ghost.TButton",
                                     command=self._toggle_reveal)
        self.eye_button.pack(side="left", padx=8)
        ttk.Button(code_row, text="换一个", command=self._rotate_code).pack(side="left")

        ttk.Label(page, text="远程协助他人", style="H1.TLabel").pack(anchor="w", pady=(26, 8))
        peer_row = ttk.Frame(page)
        peer_row.pack(fill="x")
        ttk.Entry(peer_row, textvariable=self.peer_var, width=28).pack(side="left")
        ttk.Label(peer_row, text="－", style="Muted.TLabel").pack(side="left", padx=8)
        ttk.Entry(peer_row, textvariable=self.peer_code_var, width=18).pack(side="left")
        ttk.Button(peer_row, text="连接", style="Accent.TButton", width=14,
                   command=self._connect_peer).pack(side="left", padx=14)
        ttk.Label(peer_row, text="伙伴识别码 － 验证码（可为空）", style="Muted.TLabel").pack(side="left", padx=8)

        mode = ttk.Frame(page)
        mode.pack(anchor="w", pady=(10, 0))
        self.file_mode_var = tk.StringVar(value="desktop")
        ttk.Radiobutton(mode, text="远程桌面", value="desktop",
                        variable=self.file_mode_var).pack(side="left")
        ttk.Radiobutton(mode, text="远程文件（暂不支持）", value="file",
                        variable=self.file_mode_var, state="disabled").pack(side="left", padx=18)

        ttk.Separator(page).pack(fill="x", pady=(20, 12))
        bar = ttk.Frame(page)
        bar.pack(fill="x")
        ttk.Label(bar, text="在线设备", style="Section.TLabel").pack(side="left")
        ttk.Button(bar, text="刷新", command=self._refresh_agents).pack(side="right")
        self.tree = ttk.Treeview(page, columns=("name", "host", "status"), show="headings", height=5,
                                 selectmode="browse")
        for column, label, width in (("name", "设备", 220), ("host", "主机", 260), ("status", "状态", 90)):
            self.tree.heading(column, text=label)
            self.tree.column(column, width=width)
        self.tree.pack(fill="both", expand=True, pady=(6, 0))
        self.tree.bind("<Double-1>", lambda _e: self._connect_selected())
        return page

    def _page_devices(self, parent):
        tk = self._tk
        ttk = self._ttk
        from ui.theme import PALETTE

        page = ttk.Frame(parent)
        head = ttk.Frame(page)
        head.pack(fill="x", pady=(4, 0))
        ttk.Label(head, textvariable=self.device_var, style="H1.TLabel").pack(side="left")
        ttk.Label(head, text="在线", style="Muted.TLabel").pack(side="left", padx=12)
        ttk.Button(head, text="刷新设备", style="Ghost.TButton",
                   command=self._refresh_agents).pack(side="right")

        preview = tk.Canvas(page, height=300, background=PALETTE["panel"], highlightthickness=0)
        preview.pack(fill="x", pady=14)
        preview.create_rectangle(0, 0, 4000, 4000, fill="#182A44", outline="")
        for index in range(0, 300, 6):
            shade = 0x18 + index // 8
            preview.create_line(0, index, 4000, index + 40,
                                fill=f"#{shade:02x}{shade + 0x12:02x}{shade + 0x28:02x}")
        preview.create_text(0, 0, text="")
        self.preview = preview
        preview.bind("<Configure>", lambda _e: self._draw_preview())

        cards = ttk.Frame(page)
        cards.pack(fill="x")
        for title, command in (
            ("远程文件", self._pick_file),
            ("剪贴板同步", self._toggle_clipboard),
            ("画面设置", lambda: self._show_page("设置", "画面")),
            ("会话记录", lambda: self._show_histories()),
        ):
            card = ttk.Frame(cards, style="Card.TFrame", padding=14)
            card.pack(side="left", padx=(0, 10))
            ttk.Button(card, text=title, style="Ghost.TButton", command=command).pack()

        ttk.Label(page, text="会话记录", style="Section.TLabel").pack(anchor="w", pady=(18, 6))
        self.history = ttk.Treeview(page, columns=("agent", "viewer", "start", "duration", "frames"),
                                    show="headings", height=5)
        for column, label, width in (
            ("agent", "设备", 180),
            ("viewer", "操作人", 120),
            ("start", "开始时间", 180),
            ("duration", "时长(秒)", 100),
            ("frames", "帧数", 90),
        ):
            self.history.heading(column, text=label)
            self.history.column(column, width=width)
        self.history.pack(fill="both", expand=True)
        refresh = ttk.Frame(page)
        refresh.pack(fill="x", pady=(8, 0))
        ttk.Button(refresh, text="刷新记录",
                   command=lambda: self.viewer_commands.put(("refresh_sessions", None))).pack(side="left")
        return page

    def _page_session(self, parent):
        tk = self._tk
        ttk = self._ttk

        page = ttk.Frame(parent)
        head = ttk.Frame(page)
        head.pack(fill="x")
        ttk.Label(head, text="远程会话", style="H1.TLabel").pack(side="left")
        ttk.Label(head, textvariable=self.metric_var, style="Muted.TLabel").pack(side="right")

        self.canvas = tk.Canvas(page, background="#0B0C0E", highlightthickness=0, takefocus=1)
        self.canvas.pack(fill="both", expand=True, pady=10)
        self.canvas.bind("<Configure>", lambda _e: self._redraw())
        self.canvas.bind("<ButtonPress-1>", lambda _e: self._send_button("left", True))
        self.canvas.bind("<ButtonRelease-1>", lambda _e: self._send_button("left", False))
        self.canvas.bind("<ButtonPress-3>", lambda _e: self._send_button("right", True))
        self.canvas.bind("<ButtonRelease-3>", lambda _e: self._send_button("right", False))
        self.canvas.bind("<Motion>", self._on_motion)
        self.canvas.bind("<MouseWheel>", self._on_wheel)
        self.canvas.bind("<KeyPress>", lambda event: self._on_key(event, True))
        self.canvas.bind("<KeyRelease>", lambda event: self._on_key(event, False))

        bar = ttk.Frame(page)
        bar.pack(fill="x")
        ttk.Button(bar, text="断开", command=self._disconnect).pack(side="left")
        ttk.Button(bar, text="上传文件…", command=self._pick_upload).pack(side="left", padx=8)
        ttk.Label(bar, textvariable=self.status_var, style="Muted.TLabel").pack(side="right")
        return page

    def _page_settings(self, parent):
        ttk = self._ttk

        page = ttk.Frame(parent)
        ttk.Label(page, text="设置", style="H1.TLabel").pack(anchor="w", pady=(4, 10))
        split = ttk.Frame(page)
        split.pack(fill="both", expand=True)

        sub = ttk.Frame(split, width=160)
        sub.pack(side="left", fill="y")
        sub.pack_propagate(False)
        right = ttk.Frame(split)
        right.pack(side="left", fill="both", expand=True, padx=(20, 0))

        panels = {
            "本机信息": self._panel_about(right),
            "常规": self._panel_general(right),
            "安全": self._panel_security(right),
            "网络": self._panel_network(right),
            "画面": self._panel_picture(right),
        }
        self._settings_panels = panels
        self._settings_buttons = {}
        for name in SETTINGS_PAGES:
            button = ttk.Button(sub, text=name, style="SubNav.TButton",
                                command=lambda n=name: self._show_settings(n))
            button.pack(fill="x", pady=1)
            self._settings_buttons[name] = button
        self._show_settings("本机信息")
        return page

    def _panel_about(self, parent):
        ttk = self._ttk
        frame = ttk.Frame(parent)
        ttk.Label(frame, text="本机信息", style="Section.TLabel").pack(anchor="w", pady=(0, 8))
        rows = (
            ("设备名", self.device_var),
            ("备注", self.note_var),
            ("识别码", self.ident_var),
        )
        for label, variable in rows:
            row = ttk.Frame(frame)
            row.pack(anchor="w", pady=3, fill="x")
            ttk.Label(row, text=label, style="Muted.TLabel", width=8).pack(side="left")
            ttk.Entry(row, textvariable=variable, width=34).pack(side="left", padx=16)

        ttk.Label(frame, text="无人值守模式", style="Section.TLabel").pack(anchor="w", pady=(14, 6))
        ttk.Checkbutton(frame, text="凭验证码免确认接入（关闭时需本机点同意）",
                        variable=self.unattended_var, command=self._apply_code_policy).pack(anchor="w")
        ttk.Label(frame, text="更新策略", style="Muted.TLabel").pack(anchor="w", pady=(8, 2))
        self._policy_row(frame)
        ttk.Button(frame, text="保存本机信息", style="Accent.TButton",
                   command=self._save_agent_config).pack(anchor="w", pady=14)

        ttk.Label(frame, text="日志", style="Section.TLabel").pack(anchor="w", pady=(6, 4))
        self.log = self._log_box(frame, height=8)
        return frame

    def _panel_general(self, parent):
        ttk = self._ttk
        frame = ttk.Frame(parent)
        ttk.Label(frame, text="常规", style="Section.TLabel").pack(anchor="w", pady=(0, 8))
        ttk.Checkbutton(frame, text="允许鼠标键盘控制", variable=self.allow_var,
                        command=self._apply_agent_settings).pack(anchor="w", pady=4)
        ttk.Checkbutton(frame, text="同步剪贴板", variable=self.clip_var,
                        command=self._apply_agent_settings).pack(anchor="w", pady=4)
        ttk.Checkbutton(frame, text="无人值守（凭验证码免确认）", variable=self.unattended_var,
                        command=self._apply_code_policy).pack(anchor="w", pady=4)
        ttk.Label(frame, text="验证码更新", style="Muted.TLabel").pack(anchor="w", pady=(12, 2))
        self._policy_row(frame)
        ttk.Label(frame, text="接收文件保存位置", style="Muted.TLabel").pack(anchor="w", pady=(12, 2))
        ttk.Label(frame, text=self.agent_config.download_dir).pack(anchor="w")

        ttk.Label(frame, text="高级：服务手动启停", style="Section.TLabel").pack(anchor="w", pady=(18, 4))
        ttk.Label(frame, text="首页开关会自动启停服务；这里只用于排查中继连不上等问题。",
                  style="Muted.TLabel").pack(anchor="w")
        manual = ttk.Frame(frame)
        manual.pack(anchor="w", pady=(6, 0))
        ttk.Button(manual, text="手动启动服务", command=self._start_agent).pack(side="left")
        ttk.Button(manual, text="手动停止服务", command=self._stop_agent).pack(side="left", padx=8)

        ttk.Button(frame, text="保存", style="Accent.TButton",
                   command=self._save_agent_config).pack(anchor="w", pady=14)
        return frame

    def _panel_security(self, parent):
        ttk = self._ttk
        frame = ttk.Frame(parent)
        ttk.Label(frame, text="安全", style="Section.TLabel").pack(anchor="w", pady=(0, 8))
        ttk.Label(frame, text="他人远控本机时，建议设置以下安全防护", style="Muted.TLabel").pack(anchor="w")
        ttk.Checkbutton(frame, text="允许他人协助本机（开启即在中继上线）", variable=self.serve_var,
                        command=self._toggle_serve).pack(anchor="w", pady=8)

        box = ttk.Frame(frame, style="Card.TFrame", padding=14)
        box.pack(fill="x", pady=(6, 10))
        ttk.Label(box, text="安全验证方式", style="Panel.TLabel",
                  font=("Microsoft YaHei UI", 11, "bold")).pack(anchor="w")
        ttk.Checkbutton(box, text="双重验证：强制主控端输入验证码（关闭无人值守后生效）",
                        variable=self.unattended_var, style="Panel.TCheckbutton",
                        command=self._apply_code_policy).pack(anchor="w", pady=8)
        ttk.Label(box, text="验证码更新", style="PanelMuted.TLabel").pack(anchor="w")
        radio_row = ttk.Frame(box, style="Card.TFrame")
        radio_row.pack(anchor="w", pady=4)
        for label, value in POLICY_CHOICES:
            ttk.Radiobutton(radio_row, text=label, value=label, variable=self.policy_var,
                            command=self._apply_code_policy).pack(side="left", padx=(0, 14))

        ttk.Label(frame, text="隐私设置", style="Section.TLabel").pack(anchor="w", pady=(12, 4))
        ttk.Checkbutton(frame, text="允许同步本机剪贴板文本", variable=self.clip_var,
                        command=self._apply_agent_settings).pack(anchor="w", pady=3)
        ttk.Checkbutton(frame, text="允许通过剪贴板与本机传输文件", variable=self.clip_var,
                        command=self._apply_agent_settings).pack(anchor="w", pady=3)
        return frame

    def _panel_network(self, parent):
        ttk = self._ttk
        from common.defaults import DEFAULT_AGENT_URL, DEFAULT_VIEWER_URL

        frame = ttk.Frame(parent)
        ttk.Label(frame, text="网络设置", style="Section.TLabel").pack(anchor="w", pady=(0, 8))
        ttk.Label(frame, text="中继地址（主控端）", style="Muted.TLabel").pack(anchor="w")
        ttk.Entry(frame, textvariable=self.viewer_url_var, width=52).pack(anchor="w", pady=(2, 0))
        ttk.Label(frame, text=f"默认 {DEFAULT_VIEWER_URL}", style="Muted.TLabel").pack(anchor="w")
        ttk.Label(frame, text="中继地址（被控端）", style="Muted.TLabel").pack(anchor="w", pady=(12, 0))
        ttk.Entry(frame, textvariable=self.agent_url_var, width=52).pack(anchor="w", pady=(2, 0))
        ttk.Label(frame, text=f"默认 {DEFAULT_AGENT_URL}", style="Muted.TLabel").pack(anchor="w")
        ttk.Label(frame, text="登录口令", style="Muted.TLabel").pack(anchor="w", pady=(12, 0))
        ttk.Entry(frame, textvariable=self.password_var, show="*", width=30).pack(anchor="w", pady=(2, 0))
        ttk.Label(frame, text="连接密钥", style="Muted.TLabel").pack(anchor="w", pady=(12, 0))
        ttk.Entry(frame, textvariable=self.key_var, show="*", width=52).pack(anchor="w", pady=(2, 0))

        bar = ttk.Frame(frame)
        bar.pack(anchor="w", pady=(16, 0))

        def restore() -> None:
            self.viewer_url_var.set(DEFAULT_VIEWER_URL)
            self.agent_url_var.set(DEFAULT_AGENT_URL)

        ttk.Button(bar, text="恢复默认", command=restore).pack(side="left")
        ttk.Button(bar, text="保存并登录", style="Accent.TButton",
                   command=self._save_and_login).pack(side="left", padx=8)
        return frame

    def _panel_picture(self, parent):
        ttk = self._ttk
        frame = ttk.Frame(parent)
        ttk.Label(frame, text="画面", style="Section.TLabel").pack(anchor="w", pady=(0, 8))
        ttk.Label(frame, text="画质", style="Muted.TLabel").pack(anchor="w", pady=(6, 0))
        ttk.Scale(frame, from_=20, to=95, variable=self.quality_var, orient="horizontal",
                  length=240).pack(anchor="w")
        ttk.Label(frame, text="缩放", style="Muted.TLabel").pack(anchor="w", pady=(10, 0))
        ttk.Scale(frame, from_=0.25, to=1.0, variable=self.scale_var, orient="horizontal",
                  length=240).pack(anchor="w")
        ttk.Label(frame, text="帧率", style="Muted.TLabel").pack(anchor="w", pady=(10, 0))
        ttk.Spinbox(frame, from_=1, to=30, textvariable=self.fps_var, width=8).pack(anchor="w")
        ttk.Checkbutton(frame, text="自适应画质（慢链路自动降低清晰度保帧率）",
                        variable=self.auto_var,
                        command=self._toggle_auto).pack(anchor="w", pady=(12, 0))
        ttk.Label(frame, textvariable=self.adaptive_note, style="Muted.TLabel").pack(anchor="w")
        ttk.Button(frame, text="应用画面设置", style="Accent.TButton",
                   command=self._send_settings).pack(anchor="w", pady=14)
        return frame

    def _policy_row(self, parent) -> None:
        ttk = self._ttk
        row = ttk.Frame(parent)
        row.pack(anchor="w")
        for label, _value in POLICY_CHOICES:
            ttk.Radiobutton(row, text=label, value=label, variable=self.policy_var,
                            command=self._apply_code_policy).pack(side="left", padx=(0, 14))

    def _log_box(self, parent, height: int = 8):
        import tkinter.scrolledtext as scrolledtext

        from ui.theme import PALETTE

        box = scrolledtext.ScrolledText(parent, height=height, wrap="word", state="disabled",
                                       background=PALETTE["panel"], foreground=PALETTE["text"],
                                       insertbackground=PALETTE["text"], borderwidth=0,
                                       font=("Consolas", 9))
        box.pack(fill="both", expand=True)
        return box

    def _pill(self, parent, variable):
        """AweSun-style round switch bound to a BooleanVar."""
        tk = self._tk
        from ui.theme import PALETTE

        canvas = tk.Canvas(parent, width=44, height=22, highlightthickness=0,
                          background=PALETTE["bg"], cursor="hand2")

        def draw() -> None:
            canvas.delete("all")
            on = bool(variable.get())
            fill = PALETTE["accent"] if on else PALETTE["panel_hover"]
            canvas.create_oval(0, 0, 22, 22, fill=fill, outline="")
            canvas.create_oval(22, 0, 44, 22, fill=fill, outline="")
            canvas.create_rectangle(11, 0, 33, 22, fill=fill, outline="")
            knob_x = 22 if on else 0
            canvas.create_oval(knob_x + 2, 2, knob_x + 20, 20, fill="#FFFFFF", outline="")

        def click(_event=None):
            variable.set(not variable.get())
            draw()
            self._apply_code_policy()

        canvas.bind("<Button-1>", click)
        canvas._draw = draw  # type: ignore[attr-defined]
        draw()
        return canvas

    # ----------------------------------------------------------- page routing
    def _show_page(self, name: str, settings_panel: str | None = None) -> None:
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
            self._draw_preview()
        if name == "会话":
            self.canvas.focus_set()
        if name == "设置" and settings_panel:
            self._show_settings(settings_panel)

    def _show_settings(self, name: str) -> None:
        for key, frame in getattr(self, "_settings_panels", {}).items():
            if key == name:
                frame.pack(fill="both", expand=True)
            else:
                frame.pack_forget()
        for key, button in getattr(self, "_settings_buttons", {}).items():
            button.configure(style="SubNavOn.TButton" if key == name else "SubNav.TButton")
        self.active_settings = name

    def _show_histories(self) -> None:
        self._show_page("设备")
        self.viewer_commands.put(("refresh_sessions", None))

    # ------------------------------------------------------------ device page
    def _draw_preview(self) -> None:
        preview = getattr(self, "preview", None)
        if preview is None:
            return
        width = max(320, preview.winfo_width())
        preview.delete("overlay")
        preview.create_oval(width / 2 - 22, 92, width / 2 + 22, 136, outline="#8FB8FF", width=2,
                           tags="overlay")
        preview.create_text(width / 2 - 6, 114, text="→", fill="#8FB8FF", tags="overlay")
        for offset, label, tag in ((-120, "桌面控制", "control"), (120, "桌面观看", "watch")):
            x = width / 2 + offset
            preview.create_text(x, 114, text=label, fill="#FFFFFF",
                                font=("Microsoft YaHei UI", 13, "bold"), tags=("overlay", tag))
            preview.tag_bind(tag, "<Button-1>", lambda _e: self._connect_selected())
        count = len(self.online_agents)
        preview.create_text(width / 2, 200, text=f"{count} 台设备在线", fill="#C9D6EA",
                            font=("Microsoft YaHei UI", 10), tags="overlay")

    # --------------------------------------------------------------- actions
    def _login(self) -> None:
        self.viewer_config.relay_url = self.viewer_url_var.get().strip()
        self.viewer_config.password = self.password_var.get()
        try:
            self.viewer_config.save()
        except OSError:
            pass
        self.status_var.set("正在登录中继…")
        self.viewer_commands.put(("login", {"url": self.viewer_config.relay_url,
                                           "password": self.viewer_config.password}))

    def _save_and_login(self) -> None:
        self.agent_config.relay_url = self.agent_url_var.get().strip()
        self.agent_config.agent_key = self.key_var.get().strip()
        self._save_agent_config()
        self._login()

    def _connect_peer(self) -> None:
        agent_id = self.peer_var.get().strip()
        if not agent_id:
            selected = self.tree.selection()
            agent_id = selected[0] if selected else ""
        if not agent_id:
            self._messagebox.showinfo("未选择设备", "请输入伙伴识别码，或先在设备列表里选一台")
            return
        self.viewer_commands.put(("login", {"url": self.viewer_url_var.get().strip(),
                                           "password": self.password_var.get()}))
        self.viewer_commands.put(("connect", {"agent_id": agent_id, "code": self.peer_code_var.get().strip()}))
        self.status_var.set(f"正在连接 {agent_id}…")
        self._show_page("会话")

    def _connect_selected(self) -> None:
        selection = self.tree.selection()
        if not selection:
            self._messagebox.showinfo("未选择设备", "请先在在线设备列表里选一台")
            return
        self.viewer_commands.put(("login", {"url": self.viewer_url_var.get().strip(),
                                           "password": self.password_var.get()}))
        self.viewer_commands.put(("connect", {"agent_id": selection[0],
                                             "code": self.peer_code_var.get().strip()}))
        self.status_var.set("等待被控端确认…")
        self._show_page("会话")

    def _disconnect(self) -> None:
        self.viewer_commands.put(("disconnect", None))
        self.connected = False
        self.status_var.set("已断开")

    def _refresh_agents(self) -> None:
        self.viewer_commands.put(("refresh_agents", None))

    def _refresh_serve_status(self) -> None:
        from desk.serve_state import serve_status_text

        self.serve_status.set(serve_status_text(bool(self.serve_var.get()), self._relay_state))

    def _toggle_serve(self) -> None:
        """首页的唯一入口：开=启动服务并等待中继确认，关=停止服务。"""
        enabled = bool(self.serve_var.get())
        self.agent_config.serve_on_start = enabled
        self._save_agent_config()
        if enabled:
            self._relay_state = "connecting"
            self._refresh_serve_status()
            self._start_agent()
        else:
            self._stop_agent()
            self._refresh_serve_status()

    def _start_agent(self) -> None:
        self.agent_config.relay_url = self.agent_url_var.get().strip()
        self.agent_config.agent_key = self.key_var.get().strip()
        self.agent_config.name = self.device_var.get().strip() or socket.gethostname()
        self._save_agent_config()
        self.agent_worker.start()
        self.agent_running = True
        self._relay_state = "connecting"
        self.status_var.set("正在连接中继…")
        self._refresh_serve_status()

    def _stop_agent(self) -> None:
        self.agent_worker.stop()
        self.agent_running = False
        self.status_var.set("已停止")
        self._refresh_serve_status()

    def _copy_ident(self) -> None:
        self.root.clipboard_clear()
        self.root.clipboard_append(self.ident_var.get())
        self.status_var.set("识别码已复制")

    def _toggle_reveal(self) -> None:
        self.code_revealed = not self.code_revealed
        if self.code_revealed:
            self.code_view.set(self._format_ident(self.code_var.get()))
            self.eye_button.configure(text="隐藏")
        else:
            self.code_view.set(mask_code(self.code_var.get()))
            self.eye_button.configure(text="显示")

    def _rotate_code(self) -> None:
        self.agent_config.rotate_session_code()
        self.code_var.set(self.agent_config.session_code)
        self.code_view.set(
            self._format_ident(self.code_var.get()) if self.code_revealed else mask_code(self.code_var.get())
        )
        self._save_agent_config()
        self._append_log(f"验证码已更新：{self.code_var.get()}")

    def _apply_code_policy(self) -> None:
        self.agent_config.session_code_policy = self._policy_value()
        self.agent_config.unattended = bool(self.unattended_var.get())
        self._save_agent_config()

    def _apply_agent_settings(self) -> None:
        self.agent_config.allow_input = bool(self.allow_var.get())
        self.agent_config.clipboard_sync = bool(self.clip_var.get())
        self._save_agent_config()
        try:
            self.agent_worker.apply_settings(allow_input=self.allow_var.get(),
                                            clipboard_sync=self.clip_var.get())
        except Exception:
            pass

    def _save_agent_config(self) -> None:
        self.agent_config.name = self.device_var.get().strip() or self.agent_config.agent_id
        self.agent_config.relay_url = self.agent_url_var.get().strip()
        self.agent_config.agent_key = self.key_var.get().strip()
        self.agent_config.quality = int(self.quality_var.get())
        self.agent_config.scale = float(self.scale_var.get())
        self.agent_config.fps = int(self.fps_var.get())
        try:
            self.agent_config.save()
        except OSError as exc:
            self._append_log(f"保存配置失败：{exc}")

    def _send_settings(self) -> None:
        self.viewer_commands.put(("settings", {
            "quality": int(self.quality_var.get()),
            "scale": float(self.scale_var.get()),
            "fps": int(self.fps_var.get()),
        }))

    def _toggle_auto(self) -> None:
        enabled = bool(self.auto_var.get())
        self.viewer_config.auto_quality = enabled
        try:
            self.viewer_config.save()
        except OSError as exc:
            self._append_log(f"保存配置失败：{exc}")
        self.viewer_commands.put(("auto_quality", enabled))
        if not enabled:
            self.adaptive_note.set("")
        self._append_log("自适应画质已开启" if enabled else "自适应画质已关闭")

    def _toggle_clipboard(self) -> None:
        self.clipboard_enabled = not self.clipboard_enabled
        self.clip_var.set(self.clipboard_enabled)

    def _pick_file(self) -> None:
        import tkinter.filedialog as filedialog

        path = filedialog.askopenfilename(title="选择要发送的文件")
        if path:
            self.agent_worker.send_file(path)

    def _pick_upload(self) -> None:
        import tkinter.filedialog as filedialog

        path = filedialog.askopenfilename(title="选择要上传到远端的文件")
        if path:
            self.viewer_commands.put(("upload", path))

    def _append_log(self, text: str) -> None:
        log = getattr(self, "log", None)
        if log is None:
            return
        log.configure(state="normal")
        log.insert("end", f"{time.strftime('%H:%M:%S')}  {text}\n")
        log.see("end")
        log.configure(state="disabled")

    # ------------------------------------------------------------ input relay
    def _frame_norm(self, event_x: float, event_y: float) -> "tuple[float, float]":
        from common.coords import canvas_to_frame_norm

        frame_w, frame_h = self.frame_size
        canvas = getattr(self, "canvas", None)
        if frame_w <= 0 or frame_h <= 0 or canvas is None:
            return 0.0, 0.0
        return canvas_to_frame_norm(
            event_x, event_y, frame_w, frame_h, canvas.winfo_width(), canvas.winfo_height()
        )

    def _on_motion(self, event) -> None:
        xn, yn = self._frame_norm(event.x, event.y)
        self.viewer_commands.put(("input", {"t": "input", "ev": "mouse_move", "x": xn, "y": yn}))

    def _send_button(self, button: str, pressed: bool) -> None:
        self.viewer_commands.put(
            ("input", {"t": "input", "ev": "mouse_down" if pressed else "mouse_up", "button": button})
        )

    def _on_wheel(self, event) -> None:
        self.viewer_commands.put(
            ("input", {"t": "input", "ev": "wheel", "delta": 120 if event.delta > 0 else -120})
        )

    def _on_key(self, event, pressed: bool) -> None:
        keysym = getattr(event, "keysym", "")
        if not keysym:
            return
        # 非 ASCII 字符（中文/日文/emoji）没有可翻译的虚拟键，改走文本注入：
        # 远端按字符本身落字，不依赖它那边的键盘布局或输入法。
        char = getattr(event, "char", "") or ""
        if pressed and len(char) == 1 and ord(char) > 0x7E:
            self.viewer_commands.put(("input", {"t": "input", "ev": "text", "text": char}))
            return
        self.viewer_commands.put(
            ("input", {"t": "input", "ev": "key_down" if pressed else "key_up", "keysym": keysym})
        )

    # ---------------------------------------------------------------- frames
    def _redraw(self) -> None:
        if self.frame_image is None:
            return
        from PIL import ImageTk

        canvas = self.canvas
        width = max(1, canvas.winfo_width())
        height = max(1, canvas.winfo_height())
        image = self.frame_image
        frame_w, frame_h = image.size
        ratio = min(width / frame_w, height / frame_h)
        draw_w, draw_h = max(1, int(frame_w * ratio)), max(1, int(frame_h * ratio))
        self._photo = ImageTk.PhotoImage(image.resize((draw_w, draw_h)))
        canvas.delete("frame")
        canvas.create_image(width // 2, height // 2, image=self._photo, tags="frame")

    def _render_frame(self, payload: dict) -> None:
        import io

        from PIL import Image

        data = payload.get("data", b"")
        header = payload.get("header") or {}
        try:
            image = Image.open(io.BytesIO(data))
            image.load()
        except (OSError, ValueError):
            return
        self.frame_image = image.convert("RGB")
        self.frame_size = (
            int(header.get("width") or image.width),
            int(header.get("height") or image.height),
        )
        self.fps_counter += 1
        now = time.time()
        if now - self.fps_window_started >= 1.0:
            self.current_fps = self.fps_counter / (now - self.fps_window_started)
            self.fps_counter = 0
            self.fps_window_started = now
            self.metric_var.set(f"{self.frame_size[0]}×{self.frame_size[1]}  {self.current_fps:.1f} fps")
        self._redraw()
        self.viewer_commands.put(("frame_recv", None))

    # ------------------------------------------------------------ event pump
    def _drain(self) -> None:
        for _ in range(200):
            try:
                kind, payload = self.agent_events.get_nowait()
            except queue.Empty:
                break
            if kind == "log":
                self._append_log(str(payload))
            elif kind == "status":
                self._relay_state = str(payload)
                self.status_var.set({"online": "在线，等待远控", "offline": "离线",
                                     "connecting": "连接中…"}.get(str(payload), str(payload)))
                self._refresh_serve_status()
            elif kind == "consent_request":
                self._prompt_consent(payload)

        for _ in range(200):
            try:
                kind, payload = self.viewer_events.get_nowait()
            except queue.Empty:
                break
            if kind == "agents":
                self.online_agents = list(payload or [])
                self._render_agents(self.online_agents)
            elif kind == "sessions":
                self._render_sessions(payload or [])
            elif kind == "frame":
                self._render_frame(payload)
            elif kind == "meta":
                self.status_var.set(f"已连接 · {payload.get('width')}×{payload.get('height')}")
                self._send_settings()
                if self.auto_var.get():
                    self.viewer_commands.put(("auto_quality", True))
            elif kind == "auto_profile":
                fps = float(payload.get("fps", 0.0) or 0.0)
                self.adaptive_note.set(
                    f"自适应中：{payload.get('scale')}× / q{payload.get('quality')}（{fps:.1f} fps）"
                )
            elif kind == "auto_quality":
                if payload:
                    self.adaptive_note.set("自适应画质已开启，等待链路测量…")
                else:
                    self.adaptive_note.set("")
            elif kind == "session_open":
                self.connected = True
                self.status_var.set(f"已连接到 {payload}")
                self._show_page("会话")
            elif kind == "session_closed":
                self.connected = False
                self.status_var.set("会话已结束")
                self.frame_image = None
                if hasattr(self, "canvas"):
                    self.canvas.delete("frame")
            elif kind == "logged_in":
                self.login_info.set(f"已登录：{payload}")
                self._refresh_agents()
            elif kind == "error":
                self.status_var.set(f"错误：{payload}")
                self._append_log(f"错误：{payload}")
            elif kind == "log":
                self.status_var.set(str(payload))
            elif kind == "file_saved":
                self._append_log(f"文件已保存：{payload}")
        self.root.after(60, self._drain)

    def _render_agents(self, agents: list) -> None:
        existing = set(self.tree.get_children())
        seen = set()
        for agent in agents:
            agent_id = str(agent.get("agent_id") or agent.get("id") or "")
            if not agent_id:
                continue
            seen.add(agent_id)
            info = agent.get("info") or {}
            values = (info.get("name") or agent_id, info.get("host") or "", "在线")
            if agent_id in existing:
                self.tree.item(agent_id, values=values)
            else:
                self.tree.insert("", "end", iid=agent_id, values=values)
        for item in existing - seen:
            self.tree.delete(item)
        self._draw_preview()

    def _render_sessions(self, sessions: list) -> None:
        self.history.delete(*self.history.get_children())
        for row in sessions:
            self.history.insert("", "end", values=(
                row.get("agent_name") or row.get("agent_id") or "",
                row.get("viewer") or "",
                row.get("started_at") or "",
                row.get("duration") or "",
                row.get("frames") or "",
            ))

    def _prompt_consent(self, payload: dict) -> None:
        viewer = payload.get("viewer", "未知用户")
        accepted = self._messagebox.askyesno("远程控制请求",
                                            f"{viewer} 请求控制本机。\n\n允许本次会话吗？",
                                            parent=self.root)
        self.agent_worker.answer_consent(str(payload.get("sid", "")), bool(accepted))

    def _on_close(self) -> None:
        try:
            self.agent_worker.stop()
        except Exception:
            pass
        try:
            self.viewer_worker.stop()
        except Exception:
            pass
        self.root.destroy()

    def run(self) -> None:
        self.root.mainloop()


def main() -> None:
    DeskWindow().run()


if __name__ == "__main__":
    main()
