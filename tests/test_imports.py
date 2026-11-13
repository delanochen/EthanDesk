"""Import-level checks: the GUI modules must load without a display."""

from __future__ import annotations


def test_agent_gui_imports():
    import agent.app as module

    assert hasattr(module, "AgentWindow")
    assert hasattr(module, "AgentWorker")


def test_viewer_gui_imports():
    import viewer.app as module

    assert hasattr(module, "ViewerWindow")
    assert hasattr(module, "ViewerWorker")


def test_viewer_client_url_normalisation():
    from viewer.client import to_http_base, to_ws_base

    assert to_http_base("wss://desk.example.com") == "https://desk.example.com"
    assert to_http_base("desk.example.com") == "https://desk.example.com"
    assert to_ws_base("https://desk.example.com") == "wss://desk.example.com"
    assert to_ws_base("http://127.0.0.1:8080") == "ws://127.0.0.1:8080"


def test_agent_config_roundtrip(tmp_path):
    from agent.config import AgentConfig

    config = AgentConfig(agent_id="123456789", agent_key="k" * 20, name="Test-PC")
    path = str(tmp_path / "agent.json")
    config.save(path)
    loaded = AgentConfig.load(path)
    assert loaded.agent_id == "123456789"
    assert loaded.name == "Test-PC"


def test_agent_config_default_id_is_nine_digits():
    from agent.config import default_id

    value = default_id()
    assert len(value) == 9
    assert value.isdigit()
