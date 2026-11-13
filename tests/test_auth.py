import pytest

from common.auth import AuthError, issue_token, verify_token


SECRET = "unit-test-secret"


def test_token_roundtrip_exposes_subject():
    token = issue_token(SECRET, "viewer", ttl=3600, now=1_000_000)
    payload = verify_token(SECRET, token, now=1_000_000)
    assert payload["sub"] == "viewer"
    assert payload["exp"] == 1_003_600


def test_token_rejects_expired():
    token = issue_token(SECRET, "viewer", ttl=60, now=1_000_000)
    with pytest.raises(AuthError):
        verify_token(SECRET, token, now=1_000_061)


def test_token_rejects_tampered_signature():
    token = issue_token(SECRET, "viewer", ttl=3600, now=1_000_000)
    body, sig = token.split(".")
    flipped = "0" if sig[0] != "0" else "1"
    with pytest.raises(AuthError):
        verify_token(SECRET, f"{body}.{flipped}{sig[1:]}", now=1_000_000)


def test_token_rejects_other_secret():
    token = issue_token(SECRET, "viewer", ttl=3600, now=1_000_000)
    with pytest.raises(AuthError):
        verify_token("another-secret", token, now=1_000_000)


def test_token_rejects_garbage():
    for bad in ("", "abc", "abc.def", "a.b.c"):
        with pytest.raises(AuthError):
            verify_token(SECRET, bad, now=1_000_000)
