"""版本检查：只做「有没有更新」的判断，网络用假 fetcher 顶掉。"""

from __future__ import annotations

import pytest

from common.update import check_update, is_newer, parse_manifest, parse_version
from common.version import APP_VERSION, app_version


def test_parse_version_accepts_a_leading_v():
    assert parse_version("v1.3.1") == (1, 3, 1)
    assert parse_version("1.3") == (1, 3)


def test_parse_version_rejects_junk():
    with pytest.raises(ValueError):
        parse_version("latest")


def test_is_newer_compares_numerically_not_lexically():
    assert is_newer("1.10.0", "1.9.9") is True
    assert is_newer("1.3.1", "1.3.0") is True
    assert is_newer("1.3.0", "1.3.0") is False
    assert is_newer("1.2", "1.2.0") is False


def test_manifest_accepts_a_bare_version():
    manifest = parse_manifest("1.4.0\n")
    assert manifest["version"] == "1.4.0"
    assert manifest["url"].startswith("https://github.com/")


def test_manifest_accepts_json_with_url():
    manifest = parse_manifest('{"version": "1.4.0", "url": "https://example.com/x.exe"}')
    assert manifest["version"] == "1.4.0"
    assert manifest["url"] == "https://example.com/x.exe"


def test_check_update_reports_a_newer_version():
    info = check_update("1.3.0", fetcher=lambda url: "1.3.1")
    assert info == {
        "version": "1.3.1",
        "url": info["url"],
        "notes": "",
        "current": "1.3.0",
    }


def test_check_update_is_quiet_when_up_to_date():
    assert check_update("1.3.1", fetcher=lambda url: "1.3.1") is None


def test_check_update_swallows_network_errors():
    def boom(url: str) -> str:
        raise OSError("no route to host")

    assert check_update("1.3.0", fetcher=boom) is None


def test_check_update_swallows_a_broken_manifest():
    assert check_update("1.3.0", fetcher=lambda url: "not-a-version") is None


def test_app_version_can_be_overridden_for_testing(monkeypatch):
    monkeypatch.setenv("ETHANDESK_APP_VERSION", "9.9.9")
    assert app_version() == "9.9.9"
    monkeypatch.delenv("ETHANDESK_APP_VERSION")
    assert app_version() == APP_VERSION
