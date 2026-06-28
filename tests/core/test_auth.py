"""core/auth primitives — password hashing + session-JWT round-trips."""

from __future__ import annotations

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
