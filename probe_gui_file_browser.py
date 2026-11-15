"""真机验双栏「远程文件」窗口：左本机、右被控端，上传 + 下载都走一遍。

跑的是 `viewer.app.ViewerWindow` 本体，走真实中继与真实被控端；只在关键节点用
PrintWindow 把窗口拍成 PNG 供人工核对。

用法：
  python probe_gui_file_browser.py [远端起始目录] [等待秒数，默认 40]
环境变量：
  ETHANDESK_RELAY_PASSWORD / ETHANDESK_AGENT_ID / ETHANDESK_SESSION_CODE
  ETHANDESK_VIEWER_URL   覆盖中继地址（默认 wss://desk.prasinospower.com）
  ETHANDESK_LOCAL_DIR    本机栏目录（默认仓库根）
  ETHANDESK_UPLOAD_FILE  要上传的本机文件名（默认 probe_list.py）
输出：%TEMP%\\remote_files_dialog.png、%TEMP%\\remote_files_after.png
"""

from __future__ import annotations

import os
import sys
import time

from agent.win_capture import capture_window
from common.defaults import DEFAULT_VIEWER_URL
from viewer.app import ViewerWindow

URL = os.environ.get("ETHANDESK_VIEWER_URL", DEFAULT_VIEWER_URL)
PASSWORD = os.environ.get("ETHANDESK_RELAY_PASSWORD", "")
AGENT_ID = os.environ.get("ETHANDESK_AGENT_ID", "")
CODE = os.environ.get("ETHANDESK_SESSION_CODE", "")
LOCAL_DIR = os.environ.get("ETHANDESK_LOCAL_DIR", os.path.dirname(os.path.abspath(__file__)))
UPLOAD_NAME = os.environ.get("ETHANDESK_UPLOAD_FILE", "probe_list.py")

OUT_DIALOG = os.path.join(os.environ.get("TEMP", "."), "remote_files_dialog.png")
OUT_AFTER = os.path.join(os.environ.get("TEMP", "."), "remote_files_after.png")


def shoot(window, path: str) -> tuple[int, int] | None:
    """PrintWindow 拍一个 Tk 窗口的客户区（不需要窗口在最前面）。"""
    window.update_idletasks()
    width = max(1, window.winfo_width())
    height = max(1, window.winfo_height())
    image = capture_window(window.winfo_id(), width, height)
    if image is None:
        return None
    image.save(path)
    return width, height


def main(remote_start: str, wait_seconds: float) -> None:
    if not AGENT_ID:
        print("RESULT failed 缺少 ETHANDESK_AGENT_ID", flush=True)
        raise SystemExit(2)

    win = ViewerWindow()
    win.url_var.set(URL)
    win.password_var.set(PASSWORD)
    win.root.geometry("1180x760+40+40")

    state = {
        "frames": 0,
        "logged_in": 0,
        "session_open": 0,
        "listings": {},      # path -> 次数
        "saved": [],
        "uploaded": [],
        "steps": [],
        "traced": 0,
    }

    original_render = win._render_frame

    def counted(payload):
        state["frames"] += 1
        return original_render(payload)

    win._render_frame = counted  # type: ignore[method-assign]

    class RecordingQueue:
        """只记事件、不改变消费顺序的替身队列。"""

        def __init__(self, inner):
            self._inner = inner

        def get_nowait(self):
            kind, payload = self._inner.get_nowait()
            if kind == "remote_list" and isinstance(payload, dict):
                path = str(payload.get("path") or "")
                state["listings"][path] = state["listings"].get(path, 0) + 1
            elif kind == "logged_in":
                state["logged_in"] += 1
            elif kind == "session_open":
                state["session_open"] += 1
            elif kind == "file_saved":
                state["saved"].append(str(payload))
            elif kind == "log" and "已上传" in str(payload):
                state["uploaded"].append(str(payload))
            if state["traced"] < 22 and kind != "frame":
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
    target_dir = remote_start or "C:\\"
    done = {"upload_checked": False, "download_asked": False}

    def dialog():
        return getattr(win, "remote_dialog", None)

    def tick() -> None:
        elapsed = time.time() - started
        dlg = dialog()

        if elapsed > 1.2 and "login" not in state["steps"]:
            state["steps"].append("login")
            win._login()
            print(f"login（{elapsed:.1f}s）", flush=True)

        if elapsed > 2.5 and state["logged_in"] > 0 and "connect" not in state["steps"]:
            state["steps"].append("connect")
            win.commands.put(("connect", {"agent_id": AGENT_ID, "code": CODE}))
            print(f"connect {AGENT_ID}/{CODE}（{elapsed:.1f}s）", flush=True)

        if elapsed > 6.0 and state["session_open"] > 0 and "dialog" not in state["steps"]:
            state["steps"].append("dialog")
            win._open_remote_files()
            print(f"打开双栏窗口（{elapsed:.1f}s）", flush=True)

        if "dialog" in state["steps"] and "panes" not in state["steps"] and elapsed > 7.0 and dlg:
            state["steps"].append("panes")
            dlg.local.path_var.set(LOCAL_DIR)
            dlg.local.refresh()
            dlg.remote_pane.path_var.set(target_dir)
            dlg.remote_pane.refresh()
            print(f"本机栏 {LOCAL_DIR}，远端栏 {target_dir}（{elapsed:.1f}s）", flush=True)

        # 远端目录第一次回来 → 拍第一张图，然后选中本机文件上传
        if "panes" in state["steps"] and state["listings"].get(target_dir, 0) >= 1 and "shot1" not in state["steps"] and dlg:
            state["steps"].append("shot1")
            size = shoot(dlg.top, OUT_DIALOG)
            print(f"dialog_shot {size} {OUT_DIALOG}", flush=True)
            print(f"local_rows {dlg.local.listbox.size()} remote_rows {dlg.remote_pane.listbox.size()}", flush=True)
            print(f"remote_pane_status {dlg.remote_pane.footer.get()}", flush=True)
            names = [str(entry.get("name", "")) for entry in dlg.local.entries]
            if UPLOAD_NAME not in names:
                print(f"RESULT failed 本机栏里没有 {UPLOAD_NAME}", flush=True)
                win.root.after(300, win.root.quit)
                return
            index = names.index(UPLOAD_NAME)
            dlg.local.listbox.selection_clear(0, "end")
            dlg.local.listbox.selection_set(index)
            dlg.upload_selected()
            print(f"上传 {UPLOAD_NAME} → {target_dir}（{elapsed:.1f}s）：{dlg.status_var.get()}", flush=True)

        # 上传完成 → 刷新远端，看目标目录里是否出现该文件
        if state["uploaded"] and not done["upload_checked"] and dlg:
            done["upload_checked"] = True
            print(f"upload_log {state['uploaded'][0]}", flush=True)
            dlg.remote_pane.refresh()

        if done["upload_checked"] and state["listings"].get(target_dir, 0) >= 2 and not done["download_asked"] and dlg:
            names = [str(entry.get("name", "")) for entry in dlg.remote_pane.entries]
            if UPLOAD_NAME not in names:
                print(f"RESULT failed 远端目录里没出现 {UPLOAD_NAME}", flush=True)
                win.root.after(300, win.root.quit)
                return
            done["download_asked"] = True
            size = shoot(dlg.top, OUT_AFTER)
            print(f"after_upload_shot {size} {OUT_AFTER}", flush=True)
            index = names.index(UPLOAD_NAME)
            dlg.remote_pane.listbox.selection_clear(0, "end")
            dlg.remote_pane.listbox.selection_set(index)
            dlg.download_selected()
            print(f"回程下载 {UPLOAD_NAME}：{dlg.status_var.get()}", flush=True)

        if state["saved"]:
            print(f"saved {state['saved'][-1]}", flush=True)
            print("RESULT ok", flush=True)
            win.root.after(600, win.root.quit)
            return

        if elapsed > wait_seconds:
            print(
                f"RESULT failed 超时 frames={state['frames']} listings={state['listings']} "
                f"uploaded={state['uploaded']} saved={state['saved']}",
                flush=True,
            )
            win.root.after(300, win.root.quit)
            return

        win.root.after(200, tick)

    win.root.after(300, tick)
    win.run()


if __name__ == "__main__":
    path = sys.argv[1] if len(sys.argv) > 1 else ""
    limit = float(sys.argv[2]) if len(sys.argv) > 2 else 40.0
    main(path, limit)
