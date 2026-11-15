"""真机验收键盘的"日常用法"：大小写、Shift 符号、Ctrl 组合键、emoji、长文本。

跟 probe_core.py 一样走客户端同一条协议（真实主控端能看到的东西），区别是这里
每一组按键都打一个 STEP 标记，方便和被控端 ``key_target.log`` 对齐：

- ``Shift+A`` 落到远端是不是大写 ``A``（远端 CapsLock 状态会暴露问题）
- ``Shift+1`` 是不是 ``!``
- ``Ctrl+A`` 之后再来一个字符，内容应该被整段替换掉（说明组合键真的到应用里了）
- emoji 与 300 字长文本不能丢、不能被截断

用法：python probe_mods.py [x] [y] [秒数]
x/y 是键盘靶子输入框中心归一化坐标。被控端 ETHANDESK_AGENT_ID / ETHANDESK_SESSION_CODE。
"""

from __future__ import annotations

import asyncio
import os
import sys

from common.defaults import DEFAULT_VIEWER_URL
from common.keys import text_for_key
from viewer.client import DeskClient

PASSWORD = os.environ.get("ETHANDESK_RELAY_PASSWORD", "")
LONG = "ABCDEFGHIJ" * 30  # 300 个字符，验证长文本不被截断
SHIFT = 0x0001
CONTROL = 0x0004


async def main() -> None:
    x = float(sys.argv[1]) if len(sys.argv) > 1 else 0.5
    y = float(sys.argv[2]) if len(sys.argv) > 2 else 0.45
    wait = float(sys.argv[3]) if len(sys.argv) > 3 else 5.0
    agent_id = os.environ.get("ETHANDESK_AGENT_ID", "")
    code = os.environ.get("ETHANDESK_SESSION_CODE", "")
    if not agent_id:
        print("RESULT failed 缺少 ETHANDESK_AGENT_ID", flush=True)
        raise SystemExit(2)

    client = DeskClient(DEFAULT_VIEWER_URL, password=PASSWORD)
    session = None

    async def key(keysym: str, down: bool) -> None:
        await session.send_text(
            {"t": "input", "ev": "key_down" if down else "key_up", "keysym": keysym}
        )

    async def tap(keysym: str) -> None:
        await key(keysym, True)
        await asyncio.sleep(0.05)
        await key(keysym, False)
        await asyncio.sleep(0.12)

    async def press(char: str, keysym: str, state: int = 0) -> None:
        """按主控端的分流发一次按键：可打印字符走字符注入，其余走虚拟键。

        和 viewer/app.py、desk/client.py 里的 _on_key 是同一条规则（common.keys
        的 text_for_key），所以这里测到的就是 GUI 真实会发出去的消息。
        """
        text = text_for_key(char, state)
        if text:
            await session.send_text({"t": "input", "ev": "text", "text": text})
            await asyncio.sleep(0.12)
        else:
            await tap(keysym)

    async def combo(modifier: str, char: str, keysym: str) -> None:
        await key(modifier, True)
        await asyncio.sleep(0.15)
        await press(char, keysym, CONTROL if modifier == "Control_L" else SHIFT)
        await key(modifier, False)
        await asyncio.sleep(0.15)

    try:
        await client.login()
        session = await client.open_session(agent_id, code)
        await session.send_text({"t": "set_quality", "scale": 1.0, "quality": 80, "fps": 15})
        await asyncio.sleep(2.0)

        await session.send_text({"t": "input", "ev": "mouse_move", "x": x, "y": y})
        await asyncio.sleep(0.4)
        await session.send_text({"t": "input", "ev": "mouse_down", "button": "left"})
        await asyncio.sleep(0.1)
        await session.send_text({"t": "input", "ev": "mouse_up", "button": "left"})
        await asyncio.sleep(0.8)

        print("STEP lowercase: keysym a", flush=True)
        await press("a", "a")

        print("STEP Shift+A -> expect uppercase A", flush=True)
        await combo("Shift_L", "A", "A")

        print("STEP comma + Shift+1 -> expect comma then !", flush=True)
        await press(",", "comma")
        await combo("Shift_L", "!", "exclam")

        print("STEP Ctrl+A then Z -> content should become Z", flush=True)
        await combo("Control_L", "a", "a")
        await asyncio.sleep(0.2)
        await session.send_text({"t": "input", "ev": "text", "text": "Z"})
        await asyncio.sleep(0.4)

        print("STEP emoji", flush=True)
        await session.send_text({"t": "input", "ev": "text", "text": "😀"})
        await asyncio.sleep(0.4)

        print("STEP long 300 chars", flush=True)
        await session.send_text({"t": "input", "ev": "text", "text": LONG})
        await asyncio.sleep(0.6)

        print("STEP Ctrl+A then BackSpace -> content should be empty", flush=True)
        await combo("Control_L", "a", "a")
        await asyncio.sleep(0.2)
        await tap("BackSpace")

        await asyncio.sleep(wait)
        print("RESULT ok", flush=True)
    except Exception as exc:
        print(f"RESULT failed {exc!r}", flush=True)
    finally:
        if session is not None:
            await session.close()
        await client.close()


asyncio.run(main())
