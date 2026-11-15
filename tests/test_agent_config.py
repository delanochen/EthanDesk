"""Agent configuration: session-code defaults, rotation and forward-compatible loading."""

import json

from agent.config import AgentConfig
from common.session_code import POLICY_DAILY, POLICY_EVERY_SESSION


def test_default_relay_points_at_the_hosted_tunnel():
    config = AgentConfig()
    assert config.relay_url == "wss://desk.prasinospower.com/ws/agent"


def test_defaults_include_six_digit_session_code():
    config = AgentConfig()
    assert len(config.session_code) == 6
    assert config.session_code.isdigit()
    assert config.session_code_policy == POLICY_EVERY_SESSION
    assert config.unattended is False


def test_rotate_session_code_uses_the_generator(monkeypatch):
    monkeypatch.setattr("agent.config.generate_code", lambda: "999999")
    config = AgentConfig(session_code="111111")
    code = config.rotate_session_code(now=1_700_000_000.0)
    assert code == "999999"
    assert config.session_code == "999999"
    assert config.code_rotated_at == 1_700_000_000.0


def test_save_and_load_round_trip(tmp_path):
    path = tmp_path / "agent.json"
    config = AgentConfig(session_code="654321", session_code_policy=POLICY_DAILY, unattended=True)
    config.save(str(path))
    loaded = AgentConfig.load(str(path))
    assert loaded.session_code == "654321"
    assert loaded.session_code_policy == POLICY_DAILY
    assert loaded.unattended is True


def test_load_falls_back_on_unknown_policy(tmp_path):
    path = tmp_path / "agent.json"
    path.write_text(json.dumps({"session_code_policy": "weird"}), encoding="utf-8")
    loaded = AgentConfig.load(str(path))
    assert loaded.session_code_policy == POLICY_EVERY_SESSION


def test_load_ignores_unknown_keys(tmp_path):
    path = tmp_path / "agent.json"
    path.write_text(json.dumps({"agent_id": "123456789", "future_option": 1}), encoding="utf-8")
    loaded = AgentConfig.load(str(path))
    assert loaded.agent_id == "123456789"
