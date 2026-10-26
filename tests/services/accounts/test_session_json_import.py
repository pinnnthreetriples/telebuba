"""Importing a ``.session`` together with the seller's ``.json`` (FAKE values only)."""

from __future__ import annotations

import json
from typing import TYPE_CHECKING

import pytest

from core.config import settings
from core.db import fetch_device_fingerprint
from schemas.accounts import AccountSessionFileImport
from services.accounts import (
    AccountActionError,
    import_account_session,
    parse_session_json,
)

if TYPE_CHECKING:
    from schemas.device_fingerprint import DeviceType
    from schemas.session_json import SellerSessionMeta

FAKE_PASSWORD = "fake-pass-1234567"
PHONE = "79990001122"


def _meta(**overrides: object) -> SellerSessionMeta:
    raw: dict[str, object] = {
        "twoFA": FAKE_PASSWORD,
        "device": "Samsung Galaxy S23",
        "sdk": "Android 14",
        "app_version": "10.9.1",
        "lang_code": "ru",
        "system_lang_code": "ru-RU",
        "phone": PHONE,
        "id": 1,
    }
    raw.update(overrides)
    return parse_session_json(json.dumps(raw).encode())


def _import(
    meta: SellerSessionMeta | None,
    *,
    device: DeviceType = "desktop",
) -> AccountSessionFileImport:
    return AccountSessionFileImport(
        filename=f"{PHONE}.session",
        content=b"session bytes",
        session_device=device,
        meta=meta,
    )


@pytest.fixture
def recorded(monkeypatch: pytest.MonkeyPatch) -> list[tuple[str, str]]:
    calls: list[tuple[str, str]] = []

    async def _record(account_id: str, password: str) -> bool:
        calls.append((account_id, password))
        return True

    monkeypatch.setattr("services.accounts.sessions.record_account_twofa", _record)
    return calls


@pytest.fixture
def mobile_keys(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(settings.telegram, "mobile_api_id", 6)
    monkeypatch.setattr(settings.telegram, "mobile_api_hash", "mobile-hash")


def test_parse_session_json_refuses_garbage_with_the_code() -> None:
    with pytest.raises(AccountActionError) as excinfo:
        parse_session_json(b"nope")

    assert str(excinfo.value) == "session_json_invalid"


@pytest.mark.asyncio
async def test_android_json_stores_the_seller_device_and_records_the_password(
    recorded: list[tuple[str, str]],
    mobile_keys: None,  # noqa: ARG001
) -> None:
    account = await import_account_session(_import(_meta()))

    assert account.session_device == "mobile"
    assert account.phone == PHONE
    assert account.twofa_recorded is True
    assert recorded == [(PHONE, FAKE_PASSWORD)]
    fingerprint = await fetch_device_fingerprint(PHONE, "mobile")
    assert fingerprint is not None
    assert fingerprint.platform == "android"
    assert (fingerprint.device_model, fingerprint.system_version, fingerprint.app_version) == (
        "Samsung Galaxy S23",
        "Android 14",
        "10.9.1",
    )
    assert FAKE_PASSWORD not in account.model_dump_json()


@pytest.mark.asyncio
async def test_without_json_the_import_is_unchanged(recorded: list[tuple[str, str]]) -> None:
    account = await import_account_session(_import(None))

    assert account.session_device == "desktop"
    assert account.twofa_recorded is None
    assert recorded == []
    fingerprint = await fetch_device_fingerprint(PHONE, "desktop")
    assert fingerprint is not None
    assert fingerprint.platform != "android"


@pytest.mark.asyncio
async def test_json_without_a_password_records_nothing(
    recorded: list[tuple[str, str]],
    mobile_keys: None,  # noqa: ARG001
) -> None:
    account = await import_account_session(_import(_meta(twoFA=None)))

    assert account.twofa_recorded is None
    assert recorded == []


@pytest.mark.asyncio
async def test_a_phone_that_is_not_the_file_stem_is_refused_before_anything_is_written(
    recorded: list[tuple[str, str]],
) -> None:
    with pytest.raises(AccountActionError) as excinfo:
        await import_account_session(_import(_meta(phone="79990009999")))

    assert str(excinfo.value) == "session_json_phone_mismatch"
    assert not (settings.telegram.session_dir / f"{PHONE}.session").exists()
    assert recorded == []


@pytest.mark.asyncio
async def test_android_json_needs_the_mobile_keys(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(settings.telegram, "mobile_api_id", None)
    monkeypatch.setattr(settings.telegram, "mobile_api_hash", None)

    with pytest.raises(AccountActionError) as excinfo:
        await import_account_session(_import(_meta()))

    assert str(excinfo.value) == "mobile_api_not_configured"


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "failure",
    [AccountActionError("twofa_current_password_invalid"), OSError()],
    ids=["refused", "network"],
)
async def test_an_unverified_password_fails_softly_and_the_import_stands(
    monkeypatch: pytest.MonkeyPatch,
    mobile_keys: None,  # noqa: ARG001
    failure: Exception,
) -> None:
    async def _refuse(account_id: str, password: str) -> bool:  # noqa: ARG001
        raise failure

    monkeypatch.setattr("services.accounts.sessions.record_account_twofa", _refuse)

    account = await import_account_session(_import(_meta()))

    assert account.account_id == PHONE
    assert account.twofa_recorded is False
