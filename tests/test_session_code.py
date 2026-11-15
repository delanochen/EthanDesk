"""Contracts for the short numeric code that guards a remote session."""

import datetime as dt

from common.session_code import (
    POLICY_DAILY,
    POLICY_EVERY_SESSION,
    POLICY_NEVER,
    generate_code,
    matches,
    normalise,
    should_rotate,
)


def _ts(year: int, month: int, day: int, hour: int = 12) -> float:
    return dt.datetime(year, month, day, hour).timestamp()


def test_generate_code_is_six_digits():
    for _ in range(20):
        code = generate_code()
        assert len(code) == 6
        assert code.isdigit()


def test_normalise_strips_spaces_and_dashes():
    assert normalise(" 123-456 ") == "123456"


def test_normalise_rejects_letters():
    assert normalise("12a456") == ""


def test_normalise_rejects_long_values():
    assert normalise("12345678") == ""


def test_matches_accepts_formatted_supplied_code():
    assert matches("123456", " 123 456 ") is True


def test_matches_rejects_different_code():
    assert matches("123456", "654321") is False


def test_matches_rejects_missing_configuration():
    assert matches("", "123456") is False


def test_matches_rejects_blank_input():
    assert matches("123456", "") is False


def test_every_session_policy_always_rotates():
    assert should_rotate(POLICY_EVERY_SESSION, _ts(2026, 11, 14, 9), _ts(2026, 11, 14, 10)) is True


def test_daily_policy_keeps_code_within_the_same_day():
    assert should_rotate(POLICY_DAILY, _ts(2026, 11, 14, 8), _ts(2026, 11, 14, 20)) is False


def test_daily_policy_rotates_on_the_next_day():
    assert should_rotate(POLICY_DAILY, _ts(2026, 11, 14, 8), _ts(2026, 11, 15, 8)) is True


def test_never_policy_does_not_rotate():
    assert should_rotate(POLICY_NEVER, _ts(2026, 11, 1, 8), _ts(2026, 12, 1, 8)) is False


def test_unknown_policy_never_rotates():
    assert should_rotate("weird", _ts(2026, 11, 1, 8), _ts(2026, 12, 1, 8)) is False
