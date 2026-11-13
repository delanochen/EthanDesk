"""Agent presence registry and the SQLite-backed session log."""

from __future__ import annotations

import sqlite3
import threading
import time
from dataclasses import dataclass, field
from typing import Any, Callable

DEFAULT_TIMEOUT = 30.0


@dataclass
class AgentRecord:
    id: str
    name: str
    host: str
    os_name: str
    version: str = ""
    last_seen: float = 0.0
    online: bool = True
    session_id: str | None = None

    def as_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "name": self.name,
            "host": self.host,
            "os": self.os_name,
            "version": self.version,
            "online": self.online,
            "last_seen": self.last_seen,
            "session_id": self.session_id,
        }


class Registry:
    """In-memory presence table; correctness comes from heartbeats plus this timeout."""

    def __init__(self, timeout: float = DEFAULT_TIMEOUT, clock: Callable[[], float] = time.time):
        self._timeout = timeout
        self._clock = clock
        self._agents: dict[str, AgentRecord] = {}

    @property
    def timeout(self) -> float:
        return self._timeout

    def register(
        self,
        agent_id: str,
        name: str = "",
        host: str = "",
        os_name: str = "",
        version: str = "",
    ) -> AgentRecord:
        record = AgentRecord(
            id=agent_id,
            name=name or agent_id,
            host=host,
            os_name=os_name,
            version=version,
            last_seen=self._clock(),
            online=True,
        )
        self._agents[agent_id] = record
        return record

    def heartbeat(self, agent_id: str) -> bool:
        record = self._agents.get(agent_id)
        if record is None:
            return False
        record.last_seen = self._clock()
        record.online = True
        return True

    def unregister(self, agent_id: str) -> bool:
        return self._agents.pop(agent_id, None) is not None

    def set_session(self, agent_id: str, session_id: str | None) -> None:
        record = self._agents.get(agent_id)
        if record is not None:
            record.session_id = session_id

    def get(self, agent_id: str) -> AgentRecord | None:
        return self._agents.get(agent_id)

    def refresh_online(self) -> int:
        """Mark stale agents offline; returns how many changed state."""
        changed = 0
        now = self._clock()
        for record in self._agents.values():
            if record.online and (now - record.last_seen) > self._timeout:
                record.online = False
                changed += 1
        return changed

    def is_online(self, agent_id: str) -> bool:
        record = self._agents.get(agent_id)
        return bool(record and record.online)

    def list_agents(self) -> list[dict[str, Any]]:
        self.refresh_online()
        agents = sorted(self._agents.values(), key=lambda record: record.name.lower())
        return [record.as_dict() for record in agents]


_SCHEMA = """
CREATE TABLE IF NOT EXISTS sessions (
    session_id TEXT PRIMARY KEY,
    agent_id   TEXT NOT NULL,
    viewer     TEXT NOT NULL DEFAULT '',
    started_at REAL NOT NULL,
    ended_at   REAL,
    frames     INTEGER NOT NULL DEFAULT 0
);
"""


class SessionLog:
    """Durable record of who controlled which machine, for the management console."""

    def __init__(self, path: str) -> None:
        self._lock = threading.Lock()
        self._connection = sqlite3.connect(str(path), check_same_thread=False)
        self._connection.row_factory = sqlite3.Row
        with self._lock:
            self._connection.executescript(_SCHEMA)
            self._connection.commit()

    def record_open(self, session_id: str, agent_id: str, viewer: str, started_at: float) -> None:
        with self._lock:
            self._connection.execute(
                "INSERT OR REPLACE INTO sessions (session_id, agent_id, viewer, started_at, ended_at, frames)"
                " VALUES (?, ?, ?, ?, NULL, 0)",
                (session_id, agent_id, viewer, started_at),
            )
            self._connection.commit()

    def record_close(self, session_id: str, ended_at: float, frames: int = 0) -> None:
        with self._lock:
            self._connection.execute(
                "UPDATE sessions SET ended_at = ?, frames = ? WHERE session_id = ?",
                (ended_at, int(frames), session_id),
            )
            self._connection.commit()

    def recent(self, limit: int = 50) -> list[dict[str, Any]]:
        with self._lock:
            cursor = self._connection.execute(
                "SELECT session_id, agent_id, viewer, started_at, ended_at, frames"
                " FROM sessions ORDER BY started_at DESC LIMIT ?",
                (int(limit),),
            )
            return [dict(row) for row in cursor.fetchall()]

    def close(self) -> None:
        with self._lock:
            self._connection.close()
