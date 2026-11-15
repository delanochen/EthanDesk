"""被试控端本机弹出的点击靶子：把收到的点击写进日志，用来验证远控点击真的落地。

用法：python click_target.py [存活秒数，默认 45]
"""

from __future__ import annotations

import os
import sys
import time
import tkinter as tk

from common.dpi import ensure_dpi_aware, tk_scaling

LOG = os.path.join(os.environ.get("TEMP", "."), "click_target.log")


def main() -> None:
    seconds = float(sys.argv[1]) if len(sys.argv) > 1 else 45.0
    ensure_dpi_aware()
    root = tk.Tk()
    tk_scaling(root)
    root.title("点击靶子")
    root.geometry("240x160+840+460")
    root.configure(background="#1B2430")
    label = tk.Label(root, text="等待远端点击…", fg="#E8EEF6", bg="#1B2430",
                     font=("Microsoft YaHei UI", 11))
    label.pack(expand=True)

    with open(LOG, "w", encoding="utf-8") as handle:
        handle.write("ready\n")

    def record(kind: str) -> None:
        stamp = time.strftime("%H:%M:%S")
        with open(LOG, "a", encoding="utf-8") as handle:
            handle.write(f"{kind} {stamp}\n")
        label.configure(text=f"收到 {kind} {stamp}")

    root.bind("<Button-1>", lambda _e: record("left"))
    root.bind("<Button-3>", lambda _e: record("right"))
    root.after(int(seconds * 1000), root.destroy)
    root.mainloop()


if __name__ == "__main__":
    main()
