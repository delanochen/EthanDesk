"""Agent configuration persisted next to the user's roaming profile."""

from __future__ import annotations

import json
import os
import socket
import uuid
from dataclasses import asdict, dataclass, field

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
    relay_url: str = "ws://127.0.0.1:8080/ws/agent"
    agent_id: str = field(default_factory=default_id)
    agent_key: str = "change-me"
    name: str = field(default_factory=lambda: socket.gethostname())
    fps: int = 15
    quality: int = 70
    scale: float = 1.0
    clipboard_sync: bool = True
    allow_input: bool = True
    download_dir: str = field(default_factory=default_download_dir)

    def info(self) -> dict:
        return {
            "name": self.name,
            "host": socket.gethostname(),
            "os": "Windows",
            "version": "1.0.1",
        }

    def save(self, path: str | None = None) -> str:
        target = path or os.path.join(config_dir(), CONFIG_NAME)
        os.makedirs(os.path.dirname(target), exist_ok=True)
        with open(target, "w", encoding="utf-8") as handle:
            json.dump(asdict(self), handle, ensure_ascii=False, indent=2)
        return target

    @classmethod
    def load(cls, path: str | None = None) -> "AgentConfig":
        target = path or os.path.join(config_dir(), CONFIG_NAME)
        if not os.path.exists(target):
            return cls()
        with open(target, "r", encoding="utf-8") as handle:
            data = json.load(handle)
        allowed = {field_name for field_name in cls().__dataclass_fields__}
        return cls(**{key: value for key, value in data.items() if key in allowed})
