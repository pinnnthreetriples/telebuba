"""A mobile authorization looks like the official Android app — or is refused.

Its fingerprint follows ``ConnectionsManager.java`` (``Build.MANUFACTURER + Build.MODEL``,
``SDK <n>``, ``<version> (<build>)``), its client connects with the Android app's own
``api_id``/``api_hash`` and ``lang_pack="android"``, and a device the account lacks or
that is not configured is refused with a stable code instead of falling back to the
desktop one. Stored fingerprints are never rewritten.
"""

from __future__ import annotations

import re
from typing import TYPE_CHECKING

import pytest

import core.repositories.device_fingerprint as fingerprint_repository
from core.config import settings
from core.db import (
    _get_engine,
    configure_database,
    create_account,
    fetch_device_fingerprint,
    insert_device_fingerprint,
)
from core.device_fingerprint import get_or_create_device_fingerprint
from core.device_fingerprint_android import (
    ANDROID_APP_VERSIONS,
    ANDROID_DEVICES,
    generate_android_fingerprint,
)
from core.repositories.device_fingerprint import record_second_device
from core.telegram_client import DeviceUnavailableError, create_telegram_client
from core.telegram_client._client import prepare_telegram_client_profile
from schemas.accounts import AccountCreate
from schemas.device_fingerprint import (
    DeviceFingerprint,
    TelegramClientProfile,
    TelegramClientRequest,
)

if TYPE_CHECKING:
    from pathlib import Path


@pytest.fixture(autouse=True)
def _isolate(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    configure_database(tmp_path / "telebuba.db")
    _get_engine()
    monkeypatch.setattr(settings.telegram, "session_dir", tmp_path / "sessions")
    monkeypatch.setattr(settings.telegram, "api_id", 111)
    monkeypatch.setattr(settings.telegram, "api_hash", "desktop-hash")
    monkeypatch.setattr(settings.telegram, "mobile_api_id", 6)
    monkeypatch.setattr(settings.telegram, "mobile_api_hash", "mobile-hash")


def test_the_android_fingerprint_has_the_official_app_shape() -> None:
    fingerprint = generate_android_fingerprint("acc", "+5215550001111")

    models = {f"{device.manufacturer}{device.model}" for device in ANDROID_DEVICES}
    assert fingerprint.device_model in models, "MANUFACTURER + MODEL, no separator"
    assert re.fullmatch(r"SDK 3[34]", fingerprint.system_version)
    assert fingerprint.app_version in ANDROID_APP_VERSIONS
    assert re.fullmatch(r"12\.(9|10)\.\d+ \(\d+\)", fingerprint.app_version)
    assert (fingerprint.platform, fingerprint.device_type) == ("android", "mobile")
    assert (fingerprint.lang_code, fingerprint.system_lang_code) == ("es", "es-MX")


def test_every_listed_device_only_claims_android_13_or_14() -> None:
    assert all(set(device.sdk_levels) <= {33, 34} for device in ANDROID_DEVICES)
    assert "samsungSM-S918B" in {f"{d.manufacturer}{d.model}" for d in ANDROID_DEVICES}


def _profile(tmp_path: Path, device_type: str) -> TelegramClientProfile:
    if device_type == "mobile":
        device = generate_android_fingerprint("acc", None)
    else:
        device = DeviceFingerprint(
            account_id="acc",
            platform="windows",
            device_model="Desktop",
            system_version="Windows 11",
            app_version="5.6.3 x64",
            lang_code="en",
            system_lang_code="en-US",
        )
    return TelegramClientProfile(
        account_id="acc",
        device_type=device.device_type,
        session_path=str(tmp_path / f"acc_{device_type}"),
        receive_updates=False,
        device=device,
    )


def test_a_mobile_client_connects_as_the_android_app(tmp_path: Path) -> None:
    client = create_telegram_client(_profile(tmp_path, "mobile"))

    assert (client.api_id, client.api_hash) == (6, "mobile-hash")
    assert client._init_request.lang_pack == "android"
    assert client._init_request.device_model.startswith(("samsung", "Google", "Xiaomi"))
    client.session.close()  # ty: ignore[unresolved-attribute]


def test_a_desktop_client_keeps_the_desktop_app_credentials(tmp_path: Path) -> None:
    client = create_telegram_client(_profile(tmp_path, "desktop"))

    assert (client.api_id, client.api_hash) == (111, "desktop-hash")
    assert client._init_request.lang_pack == ""
    client.session.close()  # ty: ignore[unresolved-attribute]


async def _desktop_account() -> None:
    await create_account(AccountCreate(account_id="acc", session_name="acc", phone="+15550001111"))
    await get_or_create_device_fingerprint("acc")


@pytest.mark.asyncio
async def test_a_device_the_account_lacks_is_refused_not_replaced() -> None:
    await _desktop_account()

    with pytest.raises(DeviceUnavailableError) as refused:
        await prepare_telegram_client_profile(
            TelegramClientRequest(account_id="acc", device_type="mobile"),
        )

    assert refused.value.code == "device_missing"
    assert await fetch_device_fingerprint("acc", "mobile") is None, "no phone is invented"


@pytest.mark.asyncio
async def test_an_unconfigured_mobile_is_refused(monkeypatch: pytest.MonkeyPatch) -> None:
    await _desktop_account()
    await record_second_device("acc_mobile", generate_android_fingerprint("acc", None))
    monkeypatch.setattr(settings.telegram, "mobile_api_id", 0)

    with pytest.raises(DeviceUnavailableError) as refused:
        await prepare_telegram_client_profile(
            TelegramClientRequest(account_id="acc", device_type="mobile"),
        )

    assert refused.value.code == "mobile_api_not_configured"


@pytest.mark.asyncio
async def test_each_device_opens_its_own_session_file(tmp_path: Path) -> None:
    await _desktop_account()
    await record_second_device("acc_mobile", generate_android_fingerprint("acc", None))

    desktop = await prepare_telegram_client_profile(TelegramClientRequest(account_id="acc"))
    mobile = await prepare_telegram_client_profile(
        TelegramClientRequest(account_id="acc", device_type="mobile"),
    )
    named = await prepare_telegram_client_profile(
        TelegramClientRequest(account_id="acc", session_name="acc_mobile"),
    )

    assert (desktop.device_type, desktop.session_path) == (
        "desktop",
        str(tmp_path / "sessions" / "acc"),
    )
    assert (mobile.device_type, mobile.session_path) == (
        "mobile",
        str(tmp_path / "sessions" / "acc_mobile"),
    )
    assert named.device == mobile.device, "a named second session is opened as its own device"


@pytest.mark.asyncio
async def test_a_stored_fingerprint_is_never_rewritten() -> None:
    await _desktop_account()
    first = await get_or_create_device_fingerprint("acc")

    again = await get_or_create_device_fingerprint("acc")
    clash = await insert_device_fingerprint(first.model_copy(update={"device_model": "Other PC"}))

    assert again == first
    assert clash == first, "a second insert returns the stored row instead of replacing it"
    assert await fetch_device_fingerprint("acc", "desktop") == first
    assert not hasattr(fingerprint_repository, "upsert_device_fingerprint")
