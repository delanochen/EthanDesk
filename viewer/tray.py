"""托盘图标：装好/直接跑的客户端在桌面右下角留一个图标，关窗口只是收起。

pystray 跑在自己的线程里，Tk 只能留在主线程，所以这里所有回调都通过
``root.after`` 回到 Tk 线程再动窗口，绝不从托盘线程碰控件。
"""

from __future__ import annotations

import os
import threading
from typing import Callable, Optional


class TrayUnavailable(Exception):
    """当前环境没有托盘（缺 pystray、或缺图像后端）。"""


def _asset_path(name: str) -> str:
    here = os.path.dirname(os.path.abspath(__file__))
    assets = os.path.join(os.path.dirname(here), "assets")
    return os.path.join(assets, name)


def load_icon_image():
    """优先用打包进 exe 的 assets/ethandesk.png；没有就现画一个。"""
    from PIL import Image, ImageDraw

    path = _asset_path("ethandesk.png")
    if os.path.exists(path):
        try:
            return Image.open(path).convert("RGBA")
        except OSError:
            pass
    image = Image.new("RGBA", (64, 64), (0, 0, 0, 0))
    draw = ImageDraw.Draw(image)
    draw.rounded_rectangle((4, 4, 60, 60), radius=14, fill=(37, 99, 235, 255))
    draw.rectangle((20, 22, 44, 30), fill=(255, 255, 255, 255))
    draw.rectangle((20, 34, 44, 42), fill=(255, 255, 255, 255))
    draw.rectangle((20, 46, 38, 52), fill=(255, 255, 255, 255))
    return image


class TrayController:
    """一个托盘图标 + 两条菜单：打开主窗口、退出。"""

    def __init__(
        self,
        title: str,
        on_show: Callable[[], None],
        on_quit: Callable[[], None],
        tooltip: str = "",
    ) -> None:
        self.title = title
        self.tooltip = tooltip or title
        self._on_show = on_show
        self._on_quit = on_quit
        self._icon = None
        self._thread: Optional[threading.Thread] = None

    @property
    def running(self) -> bool:
        return self._icon is not None

    def start(self) -> None:
        if self._icon is not None:
            return
        try:
            import pystray
            from pystray import Menu, MenuItem
        except Exception as exc:  # pystray 依赖的托盘后端可能缺失
            raise TrayUnavailable(str(exc)) from exc

        icon = pystray.Icon(
            "ethandesk",
            load_icon_image(),
            self.tooltip,
            menu=Menu(
                MenuItem("打开 EthanDesk", lambda *_: self._on_show(), default=True),
                MenuItem("退出", lambda *_: self._on_quit()),
            ),
        )
        self._icon = icon
        self._thread = threading.Thread(target=icon.run, name="ethandesk-tray", daemon=True)
        self._thread.start()

    def stop(self) -> None:
        icon = self._icon
        self._icon = None
        if icon is None:
            return
        try:
            icon.stop()
        except Exception:
            pass

    def notify(self, message: str, title: str = "") -> None:
        """气泡提示；后端不支持就静默跳过。"""
        icon = self._icon
        if icon is None:
            return
        try:
            icon.notify(message, title or self.title)
        except Exception:
            pass
