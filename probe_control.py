"""控制闭环验证：移动→右键→ESC，全部靠轮询确认真实生效，并顺带量出输入延迟。

轮询而不用固定 sleep，是因为慢链路上一条输入消息可能要在路上走好几秒：固定
等待会把“还没到”误判成“没生效”。

用法：python probe_control.py [轮询上限秒数，默认 15]
"""

from __future__ import annotations

import asyncio
import ctypes
import os
import time
from ctypes import wintypes

from agent.config import AgentConfig
from common.defaults import DEFAULT_VIEWER_URL
from common.dpi import ensure_dpi_aware
from viewer.client import DeskClient

# 口令不进仓库：跑之前设 ETHANDESK_RELAY_PASSWORD。
PASSWORD = os.environ.get("ETHANDESK_RELAY_PASSWORD", "")
MENU_CLASS = "#32768"

ensure_dpi_aware()


def cursor_pos() -> tuple[int, int]:
    user32 = ctypes.windll.user32

    class PT(ctypes.Structure):
        _fields_ = [("x", wintypes.LONG), ("y", wintypes.LONG)]

    point = PT()
    user32.GetCursorPos(ctypes.byref(point))
    return point.x, point.y


def popup_menu_visible() -> bool:
    """True when a Win32 popup menu (class #32768) is on screen."""
    user32 = ctypes.windll.user32
    found = []

    @ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)
    def callback(hwnd, _lparam):
        if not user32.IsWindowVisible(hwnd):
            return True
        name = ctypes.create_unicode_buffer(256)
        user32.GetClassNameW(hwnd, name, 256)
        if name.value == MENU_CLASS:
            found.append(hwnd)
        return True

    user32.EnumWindows(callback, 0)
    return bool(found)


async def poll(condition, limit: float, step: float = 0.25) -> tuple[bool, float]:
    started = time.time()
    while time.time() - started < limit:
        if condition():
            return True, time.time() - started
        await asyncio.sleep(step)
    return False, time.time() - started


async def main() -> None:
    limit = float(__import__("sys").argv[1]) if len(__import__("sys").argv) > 1 else 15.0
    config = AgentConfig.load()
    client = DeskClient(DEFAULT_VIEWER_URL)
    await client.login(PASSWORD)
    session = await client.open_session(config.agent_id, config.session_code)

    async def send(message: dict) -> None:
        await session.send_text(message)

    await send({"t": "set_quality", "scale": 0.4, "quality": 45, "fps": 10})
    await asyncio.sleep(1.0)

    target = (960, 540)
    started = time.time()
    await send({"t": "input", "ev": "mouse_move", "x": 0.5, "y": 0.5})
    moved, move_delay = await poll(lambda: cursor_pos() == target, limit)
    print(f"move reached {target}: {moved} delay={move_delay:.1f}s cursor={cursor_pos()}", flush=True)

    await send({"t": "input", "ev": "mouse_down", "button": "right"})
    await send({"t": "input", "ev": "mouse_up", "button": "right"})
    opened, menu_delay = await poll(popup_menu_visible, limit)
    print(f"right-click opened menu: {opened} delay={menu_delay:.1f}s", flush=True)

    await send({"t": "input", "ev": "key_down", "keysym": "Escape"})
    await send({"t": "input", "ev": "key_up", "keysym": "Escape"})
    closed, esc_delay = await poll(lambda: not popup_menu_visible(), limit)
    print(f"ESC closed menu: {closed} delay={esc_delay:.1f}s", flush=True)

    await session.close()
    await client.close()
    print(
        f"RESULT move_ok={moved} click_ok={opened} key_ok={closed} "
        f"move_delay={move_delay:.1f}s",
        flush=True,
    )


asyncio.run(main())
