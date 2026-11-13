"""Relay configuration: TOML file plus environment overrides."""

from __future__ import annotations

import os
import tomllib
from dataclasses import dataclass, field

ENV_PASSWORD = "DESKRELAY_VIEWER_PASSWORD"
ENV_AGENT_KEY = "DESKRELAY_AGENT_KEY"
ENV_DB = "DESKRELAY_DB_PATH"
ENV_PORT = "DESKRELAY_PORT"

WEAK_VALUES = {"", "change-me", "changeme", "password", "123456"}


@dataclass
class RelayConfig:
    viewer_password: str = "change-me"
    agent_key: str = "change-me"
    agent_keys: dict[str, str] = field(default_factory=dict)
    host: str = "0.0.0.0"
    port: int = 8080
    db_path: str = "relay.db"
    token_ttl: int = 12 * 3600
    heartbeat_timeout: float = 30.0
    session_accept_timeout: float = 30.0

    def key_for(self, agent_id: str) -> str:
        """Per-agent key when configured, otherwise the shared key."""
        return self.agent_keys.get(agent_id) or self.agent_key

    def problems(self) -> list[str]:
        """Configuration risks worth refusing to start on."""
        issues: list[str] = []
        if self.viewer_password.strip().lower() in WEAK_VALUES or len(self.viewer_password) < 8:
            issues.append("viewer_password is weak: use at least 8 characters")
        if self.agent_key.strip().lower() in WEAK_VALUES or len(self.agent_key) < 16:
            issues.append("agent_key is weak: use at least 16 characters")
        return issues

    @classmethod
    def load(cls, path: str | None = None) -> "RelayConfig":
        data: dict = {}
        candidate = path or os.environ.get("DESKRELAY_CONFIG")
        if candidate and os.path.exists(candidate):
            with open(candidate, "rb") as handle:
                data = tomllib.load(handle)

        server = data.get("server", {})
        viewer = data.get("viewer", {})
        agents = data.get("agents", {})

        config = cls(
            viewer_password=viewer.get("password", cls.viewer_password),
            agent_key=agents.get("shared_key", cls.agent_key),
            agent_keys={str(key): str(value) for key, value in (agents.get("keys") or {}).items()},
            host=server.get("host", cls.host),
            port=int(server.get("port", cls.port)),
            db_path=server.get("db_path", cls.db_path),
            token_ttl=int(viewer.get("token_ttl", cls.token_ttl)),
            heartbeat_timeout=float(server.get("heartbeat_timeout", cls.heartbeat_timeout)),
            session_accept_timeout=float(
                server.get("session_accept_timeout", cls.session_accept_timeout)
            ),
        )

        config.viewer_password = os.environ.get(ENV_PASSWORD, config.viewer_password)
        config.agent_key = os.environ.get(ENV_AGENT_KEY, config.agent_key)
        config.db_path = os.environ.get(ENV_DB, config.db_path)
        if os.environ.get(ENV_PORT):
            config.port = int(os.environ[ENV_PORT])
        return config
