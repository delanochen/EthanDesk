"""Viewer settings: hosted relay default, editable override, persistence."""

import json

from common.defaults import DEFAULT_VIEWER_URL
from viewer.config import ViewerConfig


def test_default_relay_is_the_hosted_domain():
    config = ViewerConfig()
    assert DEFAULT_VIEWER_URL == "https://desk.prasinospower.com"
    assert config.relay_url == DEFAULT_VIEWER_URL


def test_relay_url_can_be_overridden_and_saved(tmp_path):
    path = tmp_path / "viewer.json"
    config = ViewerConfig(relay_url="http://127.0.0.1:8080")
    config.save(str(path))
    loaded = ViewerConfig.load(str(path))
    assert loaded.relay_url == "http://127.0.0.1:8080"


def test_load_ignores_unknown_keys(tmp_path):
    path = tmp_path / "viewer.json"
    path.write_text(json.dumps({"relay_url": "https://example.com", "future": 1}), encoding="utf-8")
    loaded = ViewerConfig.load(str(path))
    assert loaded.relay_url == "https://example.com"
