"""Config validation for ``NeurocommentSettings`` — cross-field min≤max bounds."""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from core.config import ApiSettings, AuthSettings, NeurocommentSettings


def test_reply_delay_min_must_not_exceed_max() -> None:
    with pytest.raises(ValidationError):
        NeurocommentSettings(reply_delay_min_seconds=10.0, reply_delay_max_seconds=3.0)


def test_join_delay_min_must_not_exceed_max() -> None:
    with pytest.raises(ValidationError):
        NeurocommentSettings(join_delay_min_seconds=60.0, join_delay_max_seconds=30.0)


def test_max_joins_per_account_per_day_defaults_to_conservative_cap() -> None:
    assert NeurocommentSettings().max_joins_per_account_per_day == 20


def test_max_joins_per_account_per_day_rejects_negative() -> None:
    with pytest.raises(ValidationError):
        NeurocommentSettings(max_joins_per_account_per_day=-1)


def test_auth_secret_must_be_at_least_32_bytes_when_set() -> None:
    with pytest.raises(ValidationError):
        AuthSettings(secret="too-short")


def test_auth_secret_empty_is_allowed() -> None:
    assert AuthSettings(secret="").secret == ""


def test_auth_secret_long_enough_is_accepted() -> None:
    secret = "x" * 32
    assert AuthSettings(secret=secret).secret == secret


def test_cors_wildcard_with_credentials_is_rejected() -> None:
    with pytest.raises(ValidationError):
        ApiSettings(cors_origins=["*"], cors_allow_credentials=True)


def test_cors_explicit_origins_with_credentials_is_accepted() -> None:
    api = ApiSettings(cors_origins=["https://app.example"], cors_allow_credentials=True)
    assert api.cors_origins == ["https://app.example"]


def test_cors_wildcard_without_credentials_is_accepted() -> None:
    api = ApiSettings(cors_origins=["*"], cors_allow_credentials=False)
    assert api.cors_origins == ["*"]
