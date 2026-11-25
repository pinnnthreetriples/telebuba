"""core/auth primitives — password hashing + session-JWT round-trips."""

from __future__ import annotations

from datetime import UTC, datetime

import pytest

from core import auth
from core.config import settings


@pytest.fixture(autouse=True)
def _secret(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(settings.auth, "secret", "unit-test-secret-0123456789abcdef-pad")


def test_hash_and_verify_round_trip() -> None:
    hashed = auth.hash_password("hunter2")
    assert hashed != "hunter2"
    assert auth.verify_password("hunter2", hashed) is True


def test_verify_rejects_wrong_password() -> None:
    hashed = auth.hash_password("hunter2")
    assert auth.verify_password("wrong", hashed) is False


def test_verify_rejects_garbage_hash() -> None:
    assert auth.verify_password("hunter2", "not-a-hash") is False


def test_encode_then_decode_returns_user_id() -> None:
    token = auth.encode_session_token("user-123")
    assert auth.decode_session_token(token) == "user-123"


def test_decode_rejects_a_tampered_token() -> None:
    token = auth.encode_session_token("user-123")
    assert auth.decode_session_token(token + "x") is None


def test_decode_rejects_a_token_signed_with_another_secret(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    token = auth.encode_session_token("user-123")
    monkeypatch.setattr(settings.auth, "secret", "a-different-secret-0123456789abcdef-x")
    assert auth.decode_session_token(token) is None


def test_decode_rejects_token_when_no_secret_configured(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Defense in depth: with no secret configured, issuance already 503s, so the
    # validation path must agree — "no secret => no auth". This must hold even in
    # the threat case where the JWT library would accept the empty HMAC key, so
    # we simulate that (some PyJWT versions verify an empty key) and assert our
    # own guard refuses before ever trusting the decoded payload.
    monkeypatch.setattr(settings.auth, "secret", "")
    monkeypatch.setattr(auth.jwt, "decode", lambda *_a, **_k: {"sub": "someuser", "ver": 0})
    assert auth.decode_session_claims("any.forged.token") is None


def _forge(**claims: object) -> str:
    return auth.jwt.encode(claims, settings.auth.secret, algorithm=settings.auth.algorithm)


def _now() -> int:
    return int(datetime.now(UTC).timestamp())


def test_a_fresh_token_carries_its_login_time() -> None:
    before = _now()
    claims = auth.decode_session_claims(auth.encode_session_token("user-123"))
    assert claims is not None
    assert before <= claims.auth_time <= _now()


def test_a_reissued_token_keeps_the_original_login_time() -> None:
    long_ago = _now() - 5 * 86_400
    token = auth.encode_session_token("user-123", 0, auth_time=long_ago)
    claims = auth.decode_session_claims(token)
    assert claims is not None
    assert claims.auth_time == long_ago


def test_a_token_past_the_absolute_limit_is_rejected_though_exp_is_in_the_future(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(settings.auth, "session_max_days", 30)
    token = auth.encode_session_token("user-123", 0, auth_time=_now() - 31 * 86_400)
    assert auth.jwt.decode(token, options={"verify_signature": False})["exp"] > _now()
    assert auth.decode_session_claims(token) is None


def test_a_token_just_inside_the_absolute_limit_is_accepted(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(settings.auth, "session_max_days", 30)
    token = auth.encode_session_token("user-123", 0, auth_time=_now() - 29 * 86_400)
    assert auth.decode_session_claims(token) is not None


def test_a_legacy_token_without_auth_time_counts_as_logged_in_at_iat(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(settings.auth, "session_max_days", 30)
    exp = _now() + 600
    recent = _forge(sub="u", ver=0, iat=_now() - 3600, exp=exp)
    claims = auth.decode_session_claims(recent)
    assert claims is not None
    assert abs(claims.auth_time - (_now() - 3600)) <= 1
    stale = _forge(sub="u", ver=0, iat=_now() - 31 * 86_400, exp=exp)
    assert auth.decode_session_claims(stale) is None


def test_a_token_with_neither_auth_time_nor_iat_is_rejected() -> None:
    assert auth.decode_session_claims(_forge(sub="u", ver=0, exp=_now() + 600)) is None
