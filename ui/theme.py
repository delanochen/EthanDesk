"""Shared AweSun-style dark shell used by both the agent and the viewer.

Only presentation lives here: the palette, the page names each window shows in its
sidebar, and the ttk styling applied once at start-up. Nothing in this module
imports Tk at module import time, so it stays safe to import from tests.
"""

from __future__ import annotations

PALETTE = {
    "bg": "#141519",
    "sidebar": "#1B1C21",
    "panel": "#212228",
    "panel_hover": "#2A2C33",
    "border": "#2E3037",
    "text": "#E8E9EA",
    "muted": "#95979D",
    "accent": "#3E6FE0",
    "accent_hover": "#4F7CEC",
    "ok": "#41A46A",
    "danger": "#D9534F",
}

VIEWER_PAGES = ("远程协助", "设备", "会话", "设置")
AGENT_PAGES = ("远程协助", "设置")
PAGES = VIEWER_PAGES
SETTINGS_PAGES = ("本机信息", "常规", "安全", "网络", "画面")

FONT = "Microsoft YaHei UI"


def format_ident(text: str) -> str:
    """Group an identification code the way AweSun prints it (096 570 2962)."""
    raw = str(text).strip()
    digits = "".join(ch for ch in raw if ch.isdigit())
    if not digits:
        return raw
    if len(digits) == 10:
        return f"{digits[:3]} {digits[3:6]} {digits[6:]}"
    groups = []
    while len(digits) > 3:
        groups.insert(0, digits[-3:])
        digits = digits[:-3]
    groups.insert(0, digits)
    return " ".join(groups)


def apply_theme(root):
    """Apply the dark palette to ttk and return the configured Style."""
    from tkinter import ttk

    style = ttk.Style(root)
    style.theme_use("clam")

    style.configure(".", background=PALETTE["bg"], foreground=PALETTE["text"], font=(FONT, 10))
    style.configure("TFrame", background=PALETTE["bg"])
    style.configure("Panel.TFrame", background=PALETTE["panel"])
    style.configure("Sidebar.TFrame", background=PALETTE["sidebar"])
    style.configure("Sidebar.TLabel", background=PALETTE["sidebar"], foreground=PALETTE["text"])
    style.configure("SidebarMuted.TLabel", background=PALETTE["sidebar"], foreground=PALETTE["muted"])
    style.configure("Card.TFrame", background=PALETTE["panel"])
    style.configure("TLabel", background=PALETTE["bg"], foreground=PALETTE["text"])
    style.configure("Panel.TLabel", background=PALETTE["panel"], foreground=PALETTE["text"])
    style.configure("Muted.TLabel", background=PALETTE["bg"], foreground=PALETTE["muted"])
    style.configure("PanelMuted.TLabel", background=PALETTE["panel"], foreground=PALETTE["muted"])
    style.configure("H1.TLabel", background=PALETTE["bg"], foreground=PALETTE["text"], font=(FONT, 18, "bold"))
    style.configure("Section.TLabel", background=PALETTE["bg"], foreground=PALETTE["text"], font=(FONT, 11, "bold"))
    style.configure("Ident.TLabel", background=PALETTE["bg"], foreground=PALETTE["text"], font=("Consolas", 26, "bold"))
    style.configure("Code.TLabel", background=PALETTE["bg"], foreground=PALETTE["accent_hover"],
                    font=("Consolas", 22, "bold"))
    style.configure("Warn.TLabel", background=PALETTE["bg"], foreground=PALETTE["danger"],
                    font=(FONT, 10, "bold"))

    style.configure("TButton", background=PALETTE["panel"], foreground=PALETTE["text"],
                    borderwidth=0, focusthickness=0, padding=(12, 7))
    style.map("TButton", background=[("active", PALETTE["panel_hover"])])
    style.configure("Accent.TButton", background=PALETTE["accent"], foreground="#FFFFFF")
    style.map("Accent.TButton", background=[("active", PALETTE["accent_hover"])])
    style.configure("Ghost.TButton", background=PALETTE["bg"], foreground=PALETTE["muted"])
    style.map("Ghost.TButton", background=[("active", PALETTE["panel_hover"])],
              foreground=[("active", PALETTE["text"])])
    style.configure("Nav.TButton", anchor="w", padding=(16, 10), background=PALETTE["sidebar"],
                    foreground=PALETTE["muted"], font=(FONT, 10))
    style.map("Nav.TButton", background=[("active", PALETTE["panel_hover"])],
              foreground=[("active", PALETTE["text"])])
    style.configure("NavOn.TButton", anchor="w", padding=(16, 10), background=PALETTE["panel_hover"],
                    foreground=PALETTE["text"], font=(FONT, 10))
    style.configure("SubNav.TButton", anchor="w", padding=(10, 7), background=PALETTE["bg"],
                    foreground=PALETTE["muted"])
    style.map("SubNav.TButton", background=[("active", PALETTE["panel_hover"])],
              foreground=[("active", PALETTE["text"])])
    style.configure("SubNavOn.TButton", anchor="w", padding=(10, 7), background=PALETTE["panel"],
                    foreground=PALETTE["text"])

    style.configure("TEntry", fieldbackground=PALETTE["panel"], foreground=PALETTE["text"],
                    bordercolor=PALETTE["border"], insertcolor=PALETTE["text"], padding=7)
    style.map("TEntry", fieldbackground=[("readonly", PALETTE["panel"])])
    style.configure("TCombobox", fieldbackground=PALETTE["panel"], background=PALETTE["panel"],
                    foreground=PALETTE["text"], bordercolor=PALETTE["border"], arrowcolor=PALETTE["text"])
    style.map("TCombobox",
              fieldbackground=[("readonly", PALETTE["panel"]), ("disabled", PALETTE["panel"])],
              foreground=[("readonly", PALETTE["text"]), ("disabled", PALETTE["muted"])],
              background=[("readonly", PALETTE["panel"]), ("active", PALETTE["panel_hover"])],
              selectbackground=[("readonly", PALETTE["panel"])],
              selectforeground=[("readonly", PALETTE["text"])])

    style.configure("Treeview", background=PALETTE["panel"], fieldbackground=PALETTE["panel"],
                    foreground=PALETTE["text"], borderwidth=0, rowheight=28)
    style.configure("Treeview.Heading", background=PALETTE["sidebar"], foreground=PALETTE["muted"],
                    borderwidth=0, padding=(6, 8))
    style.map("Treeview", background=[("selected", PALETTE["accent"])],
              foreground=[("selected", "#FFFFFF")])

    style.configure("TCheckbutton", background=PALETTE["bg"], foreground=PALETTE["text"])
    style.configure("Panel.TCheckbutton", background=PALETTE["panel"], foreground=PALETTE["text"])
    style.configure("TRadiobutton", background=PALETTE["bg"], foreground=PALETTE["text"])
    style.configure("TSeparator", background=PALETTE["border"])
    style.configure("TScale", background=PALETTE["panel"], troughcolor=PALETTE["sidebar"])
    style.configure("TNotebook", background=PALETTE["bg"], borderwidth=0)
    return style
