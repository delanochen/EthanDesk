"""Deployment assets must stay valid: compose parses, tunnel config parses, proxy upgrades."""

from __future__ import annotations

import pathlib

import yaml

ROOT = pathlib.Path(__file__).resolve().parents[1]
DEPLOY = ROOT / "deploy"


def test_compose_parses_and_exposes_only_loopback():
    data = yaml.safe_load((DEPLOY / "docker-compose.yml").read_text(encoding="utf-8"))
    service = data["services"]["deskrelay"]
    assert service["ports"] == ["127.0.0.1:8080:8080"]
    assert "deskrelay.env" in service["env_file"]
    assert "DESKRELAY_DB_PATH" in service["environment"]
    assert data["volumes"]["deskrelay-data"]


def test_dockerfile_copies_runtime_packages():
    text = (DEPLOY / "Dockerfile").read_text(encoding="utf-8")
    assert "requirements-relay.txt" in text
    assert "COPY common" in text and "COPY relay" in text
    assert "relay.app" in text


def test_cloudflared_ingress_points_to_relay():
    data = yaml.safe_load((DEPLOY / "cloudflared.yml").read_text(encoding="utf-8"))
    ingress = data["ingress"]
    assert ingress[0]["service"] == "http://127.0.0.1:8080"
    assert ingress[-1]["service"] == "http_status:404"


def test_nginx_enables_websocket_upgrade():
    text = (DEPLOY / "nginx.conf").read_text(encoding="utf-8")
    assert "proxy_set_header Upgrade $http_upgrade" in text
    assert 'proxy_set_header Connection "upgrade"' in text
    assert "client_max_body_size" in text


def test_env_example_lists_required_secrets():
    text = (DEPLOY / "deskrelay.env.example").read_text(encoding="utf-8")
    for key in ("DESKRELAY_VIEWER_PASSWORD", "DESKRELAY_AGENT_KEY", "DESKRELAY_DB_PATH"):
        assert key in text


def test_systemd_unit_runs_relay_module():
    text = (DEPLOY / "systemd" / "deskrelay.service").read_text(encoding="utf-8")
    assert "python -m relay.app" in text
    assert "EnvironmentFile" in text
