from relay.registry import Registry, SessionLog


class FakeClock:
    def __init__(self, start=1_000.0):
        self.now = start

    def __call__(self):
        return self.now

    def advance(self, seconds):
        self.now += seconds


def test_register_marks_agent_online():
    clock = FakeClock()
    registry = Registry(timeout=30.0, clock=clock)
    registry.register("123456789", name="Office-PC", host="office-pc", os_name="Windows")

    listed = registry.list_agents()
    assert len(listed) == 1
    record = listed[0]
    assert record["id"] == "123456789"
    assert record["online"] is True
    assert record["name"] == "Office-PC"
    assert record["os"] == "Windows"
    assert record["last_seen"] == clock.now


def test_heartbeat_refreshes_last_seen():
    clock = FakeClock()
    registry = Registry(timeout=30.0, clock=clock)
    registry.register("a1", name="A", host="h", os_name="Windows")
    clock.advance(10)
    registry.heartbeat("a1")
    assert registry.list_agents()[0]["last_seen"] == 1_010.0
    assert registry.is_online("a1") is True


def test_agent_goes_offline_after_timeout():
    clock = FakeClock()
    registry = Registry(timeout=30.0, clock=clock)
    registry.register("a1", name="A", host="h", os_name="Windows")
    clock.advance(31)
    assert registry.refresh_online() == 1
    assert registry.list_agents()[0]["online"] is False
    assert registry.is_online("a1") is False


def test_unregister_removes_agent():
    clock = FakeClock()
    registry = Registry(timeout=30.0, clock=clock)
    registry.register("a1", name="A", host="h", os_name="Windows")
    registry.unregister("a1")
    assert registry.list_agents() == []


def test_heartbeat_unknown_agent_is_ignored():
    clock = FakeClock()
    registry = Registry(timeout=30.0, clock=clock)
    assert registry.heartbeat("ghost") is False


def test_session_log_records_sessions(tmp_path):
    log = SessionLog(tmp_path / "relay.db")
    log.record_open("s1", "a1", "viewer", started_at=1_000.0)
    log.record_close("s1", ended_at=1_360.0, frames=120)

    rows = log.recent()
    assert len(rows) == 1
    row = rows[0]
    assert row["session_id"] == "s1"
    assert row["agent_id"] == "a1"
    assert row["frames"] == 120
    assert row["ended_at"] == 1_360.0
