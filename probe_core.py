"""真机验收最常用的四条链路：键盘（含中文）、剪贴板、文件上传、滚轮。

它跟客户端走同一条协议（登录 → 开会话 → set_quality → 发 input/clip/file），
所以结果就是主控端真实能用的能力。被控端的确认方式：

- 键盘：``key_target.py`` 的日志里出现同样的字符（中文也要一模一样）
- 剪贴板：被控端 ``Get-Clipboard`` 里出现同样的文本
- 文件：被控端下载目录里出现同名文件

用法：python probe_core.py [x] [y] [秒数]
x/y 是键盘靶子中心点的归一化坐标（先点一下把焦点给过去）。
被控端：ETHANDESK_AGENT_ID / ETHANDESK_SESSION_CODE；口令 ETHANDESK_RELAY_PASSWORD。
"""

from __future__ import annotations

import asyncio
import os
import sys
import tempfile
import time

from agent.files_transfer import OutgoingFile
from common.defaults import DEFAULT_VIEWER_URL
from viewer.client import DeskClient

PASSWORD = os.environ.get("ETHANDESK_RELAY_PASSWORD", "")
TEXT = "中文测试 EthanDesk 123"


async def main() -> None:
    x = float(sys.argv[1]) if len(sys.argv) > 1 else 0.5
    y = float(sys.argv[2]) if len(sys.argv) > 2 else 0.45
    wait = float(sys.argv[3]) if len(sys.argv) > 3 else 8.0
    agent_id = os.environ.get("ETHANDESK_AGENT_ID", "")
    code = os.environ.get("ETHANDESK_SESSION_CODE", "")
    if not agent_id:
        print("RESULT failed 缺少 ETHANDESK_AGENT_ID", flush=True)
        raise SystemExit(2)

    client = DeskClient(DEFAULT_VIEWER_URL, password=PASSWORD)
    session = None
    try:
        await client.login()
        session = await client.open_session(agent_id, code)
        # 先要一档"看得清"的画质，保证靶子窗口的画面是对的。
        await session.send_text({"t": "set_quality", "scale": 1.0, "quality": 80, "fps": 15})
        await asyncio.sleep(2.0)

        # 1) 把焦点给到键盘靶子，再打字（ASCII + 中文走同一条 text 事件）。
        await session.send_text({"t": "input", "ev": "mouse_move", "x": x, "y": y})
        await asyncio.sleep(0.4)
        await session.send_text({"t": "input", "ev": "mouse_down", "button": "left"})
        await asyncio.sleep(0.1)
        await session.send_text({"t": "input", "ev": "mouse_up", "button": "left"})
        await asyncio.sleep(0.6)
        print(f"sent text {TEXT!r}", flush=True)
        await session.send_text({"t": "input", "ev": "text", "text": TEXT})
        await asyncio.sleep(0.5)
        await session.send_text({"t": "input", "ev": "text", "text": "!"})
        # ASCII 走虚拟键路径，验证 keysym 映射没退化。
        for keysym in ("a", "b", "c"):
            await session.send_text({"t": "input", "ev": "key_down", "keysym": keysym})
            await asyncio.sleep(0.05)
            await session.send_text({"t": "input", "ev": "key_up", "keysym": keysym})
        await asyncio.sleep(0.3)
        await session.send_text({"t": "input", "ev": "key_down", "keysym": "Return"})
        await session.send_text({"t": "input", "ev": "key_up", "keysym": "Return"})

        # 2) 滚轮：靶子不动，但 agent 侧必须把它当成已应用的输入。
        await session.send_text({"t": "input", "ev": "wheel", "delta": -120})
        await asyncio.sleep(0.3)

        # 3) 剪贴板：主控端 → 被控端。
        clip_text = f"ETHANDESK-CLIP-{int(time.time())}"
        print(f"sent clip {clip_text}", flush=True)
        await session.send_text({"t": "clip", "dir": "to_agent", "text": clip_text})
        await asyncio.sleep(1.2)

        # 4) 文件上传：主控端 → 被控端下载目录。
        name = f"ethandes-core-{int(time.time())}.txt"
        payload = ("EthanDesk file transfer check\n" * 40).encode("utf-8")
        path = os.path.join(tempfile.gettempdir(), name)
        with open(path, "wb") as handle:
            handle.write(payload)
        outgoing = OutgoingFile(os.urandom(6).hex(), name, payload, direction="up")
        await session.send_text(outgoing.offer())
        for header, chunk in outgoing.frames():
            await session.send_bin(header, chunk)
        print(f"sent file {name} ({len(payload)}B)", flush=True)

        await asyncio.sleep(wait)
        print("RESULT ok", flush=True)
    except Exception as exc:
        print(f"RESULT failed {exc!r}", flush=True)
    finally:
        if session is not None:
            await session.close()
        await client.close()


asyncio.run(main())
