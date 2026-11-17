"""Agent configuration persisted next to the user's roaming profile."""

from __future__ import annotations

import json
import os
import socket
import time
import uuid
from dataclasses import asdict, dataclass, field

from common.defaults import DEFAULT_AGENT_URL
from common.session_code import (
    POLICIES,
    POLICY_EVERY_SESSION,
    generate_code,
    normalise,
)
from common.version import app_version

APP_DIR_NAME = "deskrelay"
CONFIG_NAME = "agent.json"


def config_dir() -> str:
    base = os.environ.get("APPDATA") or os.path.expanduser("~")
    return os.path.join(base, APP_DIR_NAME)


def default_id() -> str:
    """Stable 9-digit id derived from the machine, in the spirit of AnyDesk's address."""
    seed = f"{uuid.getnode()}-{socket.gethostname()}"
    value = int.from_bytes(uuid.uuid5(uuid.NAMESPACE_DNS, seed).bytes[:8], "big")
    return f"{value % 900000000 + 100000000:09d}"


def default_download_dir() -> str:
    return os.path.join(os.path.expanduser("~"), "Downloads", "deskrelay")


@dataclass
class AgentConfig:
    relay_url: str = DEFAULT_AGENT_URL
    agent_id: str = field(default_factory=default_id)
    agent_key: str = "change-me"
    name: str = field(default_factory=lambda: socket.gethostname())
    fps: int = 15
    quality: int = 70
    scale: float = 1.0
    clipboard_sync: bool = True
    allow_input: bool = True
    download_dir: str = field(default_factory=default_download_dir)
    session_code: str = field(default_factory=generate_code)
    session_code_policy: str = POLICY_EVERY_SESSION
    code_rotated_at: float = 0.0
    unattended: bool = False
    verify_cert: bool = True
    # 首页「允许他人协助本机」的开关状态：装上即可被协助，关掉的选择要记住。
    serve_on_start: bool = True
    # 连接本机前必须先用 Windows 账户验证；留空名单表示本机任意账户都能通过验证。
    require_login: bool = False
    login_accounts: list[str] = field(default_factory=list)

    def info(self) -> dict:
        return {
            "name": self.name,
            "host": socket.gethostname(),
            "os": "Windows",
            # 上报给中继的版本必须和 exe 里的一致，否则设备列表显示的版本永远停在旧值。
            "version": app_version(),
        }

    def rotate_session_code(self, now: float | None = None) -> str:
        """Replace the code and stamp the rotation time."""
        self.session_code = generate_code()
        self.code_rotated_at = time.time() if now is None else now
        return self.session_code

    def save(self, path: str | None = None) -> str:
        target = path or os.path.join(config_dir(), CONFIG_NAME)
        os.makedirs(os.path.dirname(target), exist_ok=True)
        with open(target, "w", encoding="utf-8") as handle:
            json.dump(asdict(self), handle, ensure_ascii=False, indent=2)
        return target

    @classmethod
    def load(cls, path: str | None = None, *, now: float | None = None) -> "AgentConfig":
        target = path or os.path.join(config_dir(), CONFIG_NAME)
        if not os.path.exists(target):
            return cls()
        with open(target, "r", encoding="utf-8") as handle:
            data = json.load(handle)
        allowed = {field_name for field_name in cls().__dataclass_fields__}
        values = {key: value for key, value in data.items() if key in allowed}
        config = cls(**values)
        if config.session_code_policy not in POLICIES:
            config.session_code_policy = POLICY_EVERY_SESSION
        if not normalise(config.session_code):
            config.session_code = generate_code()
            config.code_rotated_at = time.time() if now is None else now
        return config
