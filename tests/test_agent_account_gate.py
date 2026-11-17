"""账户门禁：连接前必须验证被控电脑的 Windows 账户，且只认名单里的那个。"""

from __future__ import annotations

import asyncio
import base64
import queue

from agent import winlogin
from agent.app import AgentWorker
from agent.config import AgentConfig
from common import credentials


class _StubWs:
    """只记录被控端发出去的消息，不需要真的连中继。"""

    def __init__(self) -> None:
        self.sent: list[dict] = []

    async def send_str(self, text: str) -> None:
        import json

        self.sent.append(json.loads(text))


def _worker(**config) -> AgentWorker:
    cfg = AgentConfig(**config)
    return AgentWorker(cfg, queue.Queue(), queue.Queue())


def _request(**extra) -> dict:
    base = {"t": "session_request", "sid": "s1", "viewer": "friend", "code": ""}
    base.update(extra)
    return base


def test_credentials_round_trip_through_a_header_value():
    header = credentials.encode("陈亦珹:P@ss+word")
    assert header.isascii()
    assert credentials.decode(header) == "陈亦珹:P@ss+word"
    assert credentials.decode("") == ""
    assert credentials.decode("!!!not-base64!!!") == ""


def test_account_matching_ignores_domain_prefix_and_case():
    assert winlogin.normalise_account("VPE-WIN2025\\Guest") == "guest"
    assert winlogin.normalise_account(".\\Guest") == "guest"
    assert winlogin.allowed("VPE-WIN2025\\guest", ["Guest"]) is True
    assert winlogin.allowed("other", ["Guest"]) is False
    # 名单为空表示本机任意账户都能通过验证（仍然要密码对）。
    assert winlogin.allowed("anyone", []) is True


def test_empty_credentials_are_refused_without_touching_win32():
    ok, reason = winlogin.verify("", "", None)
    assert ok is False
    assert reason == winlogin.BAD_CREDENTIALS


def test_account_outside_the_allow_list_is_refused():
    ok, reason = winlogin.verify("intruder", "whatever", ["Guest"])
    assert ok is False
    assert reason == winlogin.ACCOUNT_NOT_ALLOWED


def test_login_is_required_before_any_session_when_enabled():
    worker = _worker(require_login=True, login_accounts=["Guest"])
    ws = _StubWs()
    asyncio.run(worker._ask_consent(ws, _request(user="Guest", password="")))
    assert ws.sent[-1]["t"] == "session_denied"
    assert ws.sent[-1]["need_login"] is True


def test_good_credentials_open_the_session_without_a_local_prompt():
    worker = _worker(require_login=True, login_accounts=["Guest"])
    worker._verify_login = lambda user, password: (True, "")
    ws = _StubWs()
    asyncio.run(worker._ask_consent(ws, _request(user="guest", password="secret")))
    assert ws.sent[-1]["t"] == "session_accept"
    assert worker._active_session == "s1"


def test_bad_credentials_are_denied_with_a_reason_the_client_can_show():
    worker = _worker(require_login=True, login_accounts=["Guest"])
    worker._verify_login = lambda user, password: (False, winlogin.BAD_CREDENTIALS)
    ws = _StubWs()
    asyncio.run(worker._ask_consent(ws, _request(user="Guest", password="wrong")))
    assert ws.sent[-1]["t"] == "session_denied"
    assert ws.sent[-1]["code"] == winlogin.BAD_CREDENTIALS


def test_disallowed_account_is_denied_even_with_the_right_password():
    worker = _worker(require_login=True, login_accounts=["Guest"])
    worker._verify_login = lambda user, password: (False, winlogin.ACCOUNT_NOT_ALLOWED)
    ws = _StubWs()
    asyncio.run(worker._ask_consent(ws, _request(user="Administrator", password="right")))
    assert ws.sent[-1]["code"] == winlogin.ACCOUNT_NOT_ALLOWED


def test_gate_can_be_switched_at_runtime_from_the_agent_ui():
    worker = _worker()
    assert worker.config.require_login is False
    worker.apply_settings(require_login=True, login_accounts=["Guest"])
    assert worker.config.require_login is True
    assert worker.config.login_accounts == ["Guest"]


def test_login_setting_survives_a_config_round_trip(tmp_path):
    path = tmp_path / "agent.json"
    AgentConfig(require_login=True, login_accounts=["Guest", "Tester"]).save(str(path))
    loaded = AgentConfig.load(str(path))
    assert loaded.require_login is True
    assert loaded.login_accounts == ["Guest", "Tester"]


def test_account_list_splits_on_chinese_and_ascii_separators():
    from desk.client import _split_accounts

    assert _split_accounts("Guest，Tester; other ,last") == ["Guest", "Tester", "other", "last"]
    assert _split_accounts("   ") == []


def test_missing_sid_still_denies_instead_of_crashing():
    worker = _worker(require_login=True)
    ws = _StubWs()
    asyncio.run(worker._ask_consent(ws, {"t": "session_request"}))
    assert ws.sent[-1]["t"] == "session_denied"
