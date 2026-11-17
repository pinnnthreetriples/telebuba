"""A session's key always connects with the API that made it; only new keys get the new API."""

from __future__ import annotations

import pytest

from core.config import settings
from core.db import fetch_device_fingerprint
from core.telegram_client._api_credentials import (
    ApiCredentialsMissingError,
    api_credentials,
    record_key_api,
)
from core.telegram_client._client import prepare_telegram_client_profile
from schemas.accounts import AccountCreate
from schemas.device_fingerprint import DeviceFingerprint, TelegramClientRequest
from services.accounts import add_account

_LEGACY = (111, "legacy-hash")
_MOBILE = (222, "mobile-hash")
_NEW = (333, "new-hash")


@pytest.fixture(autouse=True)
def _apis(monkeypatch: pytest.MonkeyPatch) -> None:
    telegram = settings.telegram
    monkeypatch.setattr(telegram, "api_id", _LEGACY[0])
    monkeypatch.setattr(telegram, "api_hash", _LEGACY[1])
    monkeypatch.setattr(telegram, "mobile_api_id", _MOBILE[0])
    monkeypatch.setattr(telegram, "mobile_api_hash", _MOBILE[1])
    monkeypatch.setattr(telegram, "new_api_id", _NEW[0])
    monkeypatch.setattr(telegram, "new_api_hash", _NEW[1])


def _device(device_type: str = "desktop", api_id: int | None = None) -> DeviceFingerprint:
    return DeviceFingerprint(
        account_id="acc-1",
        device_type=device_type,  # ty: ignore[invalid-argument-type]
        platform="android" if device_type == "mobile" else "linux",
        device_model="PC",
        system_version="Fedora 40",
        app_version="5.0",
        lang_code="en",
        system_lang_code="en-US",
        api_id=api_id,
    )


@pytest.mark.parametrize(
    ("device", "expected"),
    [
        (_device(), _LEGACY),  # every session from before this was recorded
        (_device("mobile"), _MOBILE),
        (_device(api_id=_NEW[0]), _NEW),
        (_device(api_id=_LEGACY[0]), _LEGACY),
    ],
)
def test_a_session_connects_with_the_api_that_made_it(
    device: DeviceFingerprint, expected: tuple[int, str]
) -> None:
    assert api_credentials(device) == expected


def test_a_session_whose_api_is_no_longer_configured_is_refused_not_switched(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(settings.telegram, "new_api_hash", "")

    with pytest.raises(ApiCredentialsMissingError):
        api_credentials(_device(api_id=_NEW[0]))
    with pytest.raises(ApiCredentialsMissingError):
        api_credentials(_device(api_id=999))


@pytest.mark.asyncio
async def test_only_a_new_key_gets_the_new_api_and_keeps_it_once_recorded() -> None:
    await add_account(AccountCreate(account_id="acc-1"))

    existing = await prepare_telegram_client_profile(TelegramClientRequest(account_id="acc-1"))
    fresh = await prepare_telegram_client_profile(
        TelegramClientRequest(account_id="acc-1", new_key=True)
    )

    assert existing.device.api_id is None
    assert fresh.device.api_id == _NEW[0]
    # Nothing is stored until the sign-in succeeded...
    stored = await fetch_device_fingerprint("acc-1")
    assert stored is not None
    assert stored.api_id is None
    # ...then the key keeps its API, new_key or not.
    await record_key_api(fresh)
    after = await prepare_telegram_client_profile(TelegramClientRequest(account_id="acc-1"))
    assert after.device.api_id == _NEW[0]
    assert api_credentials(after.device) == _NEW


@pytest.mark.asyncio
async def test_without_a_new_api_a_new_key_stays_on_the_default(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(settings.telegram, "new_api_id", 0)
    await add_account(AccountCreate(account_id="acc-1"))

    fresh = await prepare_telegram_client_profile(
        TelegramClientRequest(account_id="acc-1", new_key=True)
    )
    await record_key_api(fresh)

    assert fresh.device.api_id is None
    stored = await fetch_device_fingerprint("acc-1")
    assert stored is not None
    assert stored.api_id is None
