"""被试控端的键盘靶子：把收到的每个键写进日志，用来验证远控键盘真的落字。

用法：python key_target.py [存活秒数，默认 90]

窗口里有一个输入框，日志是 ``%TEMP%\\key_target.log``：每行 ``keysym|char``，
所以既能看出 ASCII 按键，也能看出中文/emoji 有没有真的进到应用里。
"""

from __future__ import annotations

import os
import sys
import time
import tkinter as tk

from common.dpi import ensure_dpi_aware, tk_scaling

LOG = os.path.join(os.environ.get("TEMP", "."), "key_target.log")


def main() -> None:
    seconds = float(sys.argv[1]) if len(sys.argv) > 1 else 90.0
    ensure_dpi_aware()
    root = tk.Tk()
    tk_scaling(root)
    root.title("键盘靶子")
    root.geometry("360x120+600+300")
    root.configure(background="#1B2430")
    root.attributes("-topmost", True)
    root.lift()
    root.focus_force()

    entry = tk.Entry(root, font=("Microsoft YaHei UI", 12))
    entry.pack(fill="x", padx=12, pady=(18, 6))
    entry.focus_set()
    hint = tk.Label(root, text="在这里打字，内容会写进日志", fg="#E8EEF6", bg="#1B2430",
                    font=("Microsoft YaHei UI", 10))
    hint.pack()

    with open(LOG, "w", encoding="utf-8") as handle:
        handle.write("ready\n")

    def record(event) -> None:
        with open(LOG, "a", encoding="utf-8") as handle:
            handle.write(f"{event.keysym}|{event.char}\n")

    root.bind("<Key>", record)
    root.after(int(seconds * 1000), root.destroy)
    root.mainloop()


if __name__ == "__main__":
    main()
