"""Importing a ``.session`` together with the seller's ``.json`` (FAKE values only)."""

from __future__ import annotations

import json
from typing import TYPE_CHECKING

import pytest

from core.config import settings
from core.db import fetch_account_twofa_password, fetch_device_fingerprint
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
def telegram_calls(monkeypatch: pytest.MonkeyPatch) -> list[tuple[str, object]]:
    """Every Telegram action the import may issue: it must stay empty.

    The account has no proxy at import time, so any connection would go out from
    the operator's own IP.
    """
    calls: list[tuple[str, object]] = []

    async def _execute(account_id: str, action: object) -> object:
        calls.append((account_id, action))
        raise AssertionError

    monkeypatch.setattr("services.accounts.twofa.execute", _execute)
    monkeypatch.setattr("core.telegram_client.execute", _execute)
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
async def test_android_json_stores_the_seller_device_and_makes_no_telegram_call(
    telegram_calls: list[tuple[str, object]],
    mobile_keys: None,  # noqa: ARG001
) -> None:
    account = await import_account_session(_import(_meta()))

    assert account.session_device == "mobile"
    assert account.phone == PHONE
    assert telegram_calls == []
    assert await fetch_account_twofa_password(PHONE) is None
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
async def test_without_json_the_import_is_unchanged(
    telegram_calls: list[tuple[str, object]],
) -> None:
    account = await import_account_session(_import(None))

    assert account.session_device == "desktop"
    assert telegram_calls == []
    fingerprint = await fetch_device_fingerprint(PHONE, "desktop")
    assert fingerprint is not None
    assert fingerprint.platform != "android"


@pytest.mark.asyncio
async def test_a_phone_that_is_not_the_file_stem_is_refused_before_anything_is_written(
    telegram_calls: list[tuple[str, object]],
) -> None:
    with pytest.raises(AccountActionError) as excinfo:
        await import_account_session(_import(_meta(phone="79990009999")))

    assert str(excinfo.value) == "session_json_phone_mismatch"
    assert not (settings.telegram.session_dir / f"{PHONE}.session").exists()
    assert telegram_calls == []


@pytest.mark.asyncio
async def test_android_json_needs_the_mobile_keys(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(settings.telegram, "mobile_api_id", None)
    monkeypatch.setattr(settings.telegram, "mobile_api_hash", None)

    with pytest.raises(AccountActionError) as excinfo:
        await import_account_session(_import(_meta()))

    assert str(excinfo.value) == "mobile_api_not_configured"
