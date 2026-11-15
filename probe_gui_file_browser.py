"""真机验主控端「远程文件…」窗口：开真窗口 → 登录 → 连被控端 → 浏览目录 → 截图。

不是模拟：跑的是 `viewer.app.ViewerWindow` 本体，走真实中继和中继后面的被控端，
只在最后用 PrintWindow 把窗口拍成 PNG 供人工核对。

用法：
  python probe_gui_file_browser.py [起始目录] [等待秒数，默认 22]
环境变量：ETHANDESK_RELAY_PASSWORD / ETHANDESK_AGENT_ID / ETHANDESK_SESSION_CODE
          ETHANDESK_VIEWER_URL 可覆盖中继地址（默认 wss://desk.prasinospower.com）
输出：%TEMP%\\remote_files_dialog.png 与 %TEMP%\\remote_files_main.png
"""

from __future__ import annotations

import ctypes
import os
import sys
import time

from common.defaults import DEFAULT_VIEWER_URL
from agent.win_capture import capture_window
from viewer.app import ViewerWindow

URL = os.environ.get("ETHANDESK_VIEWER_URL", DEFAULT_VIEWER_URL)
PASSWORD = os.environ.get("ETHANDESK_RELAY_PASSWORD", "")
AGENT_ID = os.environ.get("ETHANDESK_AGENT_ID", "")
CODE = os.environ.get("ETHANDESK_SESSION_CODE", "")

OUT_DIALOG = os.path.join(os.environ.get("TEMP", "."), "remote_files_dialog.png")
OUT_MAIN = os.path.join(os.environ.get("TEMP", "."), "remote_files_main.png")

user32 = ctypes.windll.user32


def _frame_of(child: int) -> int:
    parent = child
    while True:
        owner = user32.GetParent(parent)
        if not owner:
            return parent
        parent = owner


def shoot(window, path: str) -> tuple[int, int] | None:
    """PrintWindow 拍一个 Tk 窗口的客户区（不需要窗口在最前面）。"""
    window.update_idletasks()
    hwnd = window.winfo_id()
    width = max(1, window.winfo_width())
    height = max(1, window.winfo_height())
    image = capture_window(hwnd, width, height)
    if image is None:
        return None
    image.save(path)
    return width, height


def main(start_path: str, wait_seconds: float, pick: str = "") -> None:
    if not AGENT_ID:
        print("RESULT failed 缺少 ETHANDESK_AGENT_ID", flush=True)
        raise SystemExit(2)

    win = ViewerWindow()
    win.url_var.set(URL)
    win.password_var.set(PASSWORD)
    win.root.geometry("1180x760+40+40")

    state = {"frames": 0, "listing": 0, "logged_in": 0, "session_open": 0,
             "steps": [], "traced": 0, "saved": []}

    original_render = win._render_frame

    def counted(payload):
        state["frames"] += 1
        return original_render(payload)

    win._render_frame = counted  # type: ignore[method-assign]

    class RecordingQueue:
        """替身队列：只记事件，不改变消费顺序（早先的版本把事件塞回队列，导致
        登录事件被反复处理，连接永远起不来）。"""

        def __init__(self, inner):
            self._inner = inner

        def get_nowait(self):
            kind, payload = self._inner.get_nowait()
            if kind == "remote_list":
                state["listing"] += 1
            elif kind == "logged_in":
                state["logged_in"] += 1
            elif kind == "session_open":
                state["session_open"] += 1
            elif kind == "file_saved":
                state["saved"].append(str(payload))
            if state["traced"] < 25 and kind not in ("frame",):
                brief = str(payload)
                if len(brief) > 90:
                    brief = brief[:90] + "…"
                print(f"  event {kind} {brief}", flush=True)
                state["traced"] += 1
            return kind, payload

        def put(self, item):
            self._inner.put(item)

        def empty(self):
            return self._inner.empty()

    win.events = RecordingQueue(win.events)  # type: ignore[assignment]

    started = time.time()
    deadline = started + wait_seconds

    def tick() -> None:
        elapsed = time.time() - started
        if elapsed > 1.2 and "login" not in state["steps"]:
            state["steps"].append("login")
            win._login()
            print(f"login 已发出（{elapsed:.1f}s）", flush=True)
        if elapsed > 2.5 and state["logged_in"] > 0 and "connect" not in state["steps"]:
            state["steps"].append("connect")
            win.commands.put(("connect", {"agent_id": AGENT_ID, "code": CODE}))
            print(f"connect {AGENT_ID}/{CODE}（{elapsed:.1f}s）", flush=True)
        if elapsed > 6.0 and state["session_open"] > 0 and "dialog" not in state["steps"]:
            state["steps"].append("dialog")
            win._open_remote_files()
            print(f"已打开远程文件窗口（{elapsed:.1f}s）", flush=True)
        if "dialog" in state["steps"] and "path" not in state["steps"] and elapsed > 7.0:
            state["steps"].append("path")
            dialog = getattr(win, "remote_dialog", None)
            if dialog is not None:
                dialog.path_var.set(start_path)
                dialog.refresh()
                print(f"请求目录 {start_path!r}（{elapsed:.1f}s）", flush=True)
        if "path" in state["steps"] and state["listing"] >= 2 and "shot" not in state["steps"]:
            state["steps"].append("shot")
            dialog = getattr(win, "remote_dialog", None)
            if dialog is not None:
                dialog.top.update_idletasks()
                dialog.top.lift()
                size = shoot(dialog.top, OUT_DIALOG)
                print(f"dialog_shot {size} {OUT_DIALOG}", flush=True)
                print(f"dialog_status {dialog.status_var.get()}", flush=True)
                entries = dialog.listbox.size()
                labels = [dialog.listbox.get(i) for i in range(min(6, entries))]
                print(f"dialog_rows {entries} {labels}", flush=True)
            main_size = shoot(win.root, OUT_MAIN)
            print(f"main_shot {main_size} {OUT_MAIN}", flush=True)
            if not pick:
                print("RESULT ok" if state["listing"] > 0 else "RESULT failed 没收到目录", flush=True)
                win.root.after(600, win.root.quit)
                return
        if "shot" in state["steps"] and pick and "pick" not in state["steps"]:
            dialog = getattr(win, "remote_dialog", None)
            if dialog is not None:
                index = next((i for i, entry in enumerate(dialog._entries)
                              if not entry.get("dir") and str(entry.get("name")) == pick), None)
                if index is None:
                    print(f"RESULT failed 列表里没有文件 {pick}", flush=True)
                    win.root.after(400, win.root.quit)
                    return
                state["steps"].append("pick")
                dialog.listbox.selection_clear(0, "end")
                dialog.listbox.selection_set(index)
                dialog.download_selected()
                print(f"已选中并下载 {pick}（{elapsed:.1f}s）：{dialog.status_var.get()}", flush=True)
        if "pick" in state["steps"] and state["saved"] and "final" not in state["steps"]:
            state["steps"].append("final")
            dialog = getattr(win, "remote_dialog", None)
            if dialog is not None:
                dialog.top.update_idletasks()
                print(f"saved {state['saved'][-1]}", flush=True)
                print(f"dialog_status {dialog.status_var.get()}", flush=True)
                shoot(dialog.top, OUT_DIALOG)
            print("RESULT ok", flush=True)
            win.root.after(600, win.root.quit)
            return
        if elapsed > wait_seconds:
            print(f"RESULT failed 超时 frames={state['frames']} listing={state['listing']}", flush=True)
            win.root.after(300, win.root.quit)
            return
        win.root.after(200, tick)

    win.root.after(300, tick)
    win.run()


if __name__ == "__main__":
    path = sys.argv[1] if len(sys.argv) > 1 else ""
    limit = float(sys.argv[2]) if len(sys.argv) > 2 else 22.0
    pick = os.environ.get("ETHANDESK_PICK_FILE", "")
    main(path, limit, pick)
