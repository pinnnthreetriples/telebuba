"""Config validation — cross-field min≤max bounds, plus the repr-secrecy guard."""

from __future__ import annotations

from typing import TYPE_CHECKING

import pytest
from pydantic import ValidationError

from core.config import (
    ApiSettings,
    AuthSettings,
    NeurocommentSettings,
    Settings,
    TelegramSettings,
    WarmingSettings,
    settings,
)

if TYPE_CHECKING:
    from pydantic_settings import BaseSettings


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


def test_challenge_thinking_budget_must_leave_room_for_the_decision_json() -> None:
    """Thoughts are billed against the output cap.

    A budget that fills it truncates every decision and turns solvable captchas
    into give_up.
    """
    with pytest.raises(ValidationError):
        NeurocommentSettings(challenge_thinking_budget=1024, challenge_max_output_tokens=1024)


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


def test_phase_daily_cap_rejects_a_cap_of_zero() -> None:
    """A cap of 0 switches off the daily gate AND the pre-cycle reservation (#208)."""
    with pytest.raises(ValidationError):
        WarmingSettings(phase_daily_cap={"intro": 0})


# --- repr secrecy -----------------------------------------------------------
# pytest's assertion rewriting dumps a model repr whenever an assertion touches a
# ``settings`` attribute chain, and this repository is public, so a failing test
# would write live credentials into an Actions log. Every secret-bearing field is
# therefore declared ``Field(repr=False)``.
#
# The guard is derived from field NAMES, not from a hand-listed set, so a newly
# added secret field is caught rather than silently unprotected: a new field whose
# name matches is presumed secret until it is either given ``repr=False`` or
# explicitly declared benign in ``_NOT_SECRET``. Fails closed on purpose.

_SECRET_NAME_SUFFIXES = (
    "_id",
    "_hash",
    "_key",
    "_token",
    "_secret",
    "_password",
    "_username",
    "_salt",
    "_dsn",
    "_credentials",
)
_SECRET_NAME_EXACT = frozenset({"secret", "password", "token", "dsn", "salt"})
# Matches the suffix rule but carries no credential: a boolean CORS switch.
_NOT_SECRET = frozenset({"cors_allow_credentials"})


def _secret_field_names(model: type[BaseSettings]) -> list[str]:
    return [
        name
        for name in model.model_fields
        if name not in _NOT_SECRET
        and (name.endswith(_SECRET_NAME_SUFFIXES) or name in _SECRET_NAME_EXACT)
    ]


def _fake_value(model: type[BaseSettings], name: str) -> object:
    """A recognisable non-secret stand-in, typed to the field and long enough.

    40 ``z``s clear ``AuthSettings``' 32-byte HMAC floor.
    """
    if model.model_fields[name].annotation is int:
        return 987654321
    return f"FAKE-{name}-{'z' * 40}"


_SECRET_NAMESPACES = [
    (namespace, model)
    for namespace, model in ((n, type(getattr(settings, n))) for n in Settings.model_fields)
    if _secret_field_names(model)
]


def test_the_secret_field_detector_is_not_vacuous() -> None:
    """Guard the guard: a broken name rule would make every case below pass trivially."""
    assert "api_hash" in _secret_field_names(TelegramSettings)
    assert "secret" in _secret_field_names(AuthSettings)
    assert _SECRET_NAMESPACES != []


@pytest.mark.parametrize(("namespace", "model"), _SECRET_NAMESPACES)
def test_secret_fields_never_reach_a_repr(namespace: str, model: type[BaseSettings]) -> None:
    """Neither the sub-model's own repr nor the aggregate ``Settings`` repr shows them."""
    fakes = {name: _fake_value(model, name) for name in _secret_field_names(model)}
    # model_validate, not the constructor: it validates this dict alone, so the
    # assertions never depend on whatever the developer's own .env happens to hold.
    nested = model.model_validate(fakes)
    aggregate = repr(Settings.model_validate({namespace: nested}))
    own = repr(nested)
    for name, value in fakes.items():
        assert model.model_fields[name].repr is False, (
            f"{namespace}.{name} looks secret-bearing but is not declared Field(repr=False)"
        )
        assert str(value) not in own, f"{namespace}.{name} leaks into repr({namespace})"
        assert str(value) not in aggregate, f"{namespace}.{name} leaks into repr(settings)"


def test_hiding_a_secret_from_repr_leaves_the_value_usable() -> None:
    """``repr=False`` hides only the repr — reads and dumps still carry the value."""
    telegram = TelegramSettings(api_hash="FAKE-hash", api_id=987654321)
    assert telegram.api_hash == "FAKE-hash"
    assert telegram.model_dump()["api_hash"] == "FAKE-hash"
    assert "session_dir" in repr(telegram)
