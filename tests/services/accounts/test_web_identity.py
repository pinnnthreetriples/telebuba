"""The web window's identity: the account's own device language, never a dead end.

The device lookup runs against the real database, so "which of an account's two
devices" is answered by the repository #521 introduced rather than by a stub. The
locale fallback is driven through a fake launch that refuses one locale the way
Chrome's ``Emulation.setLocaleOverride`` does.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

import pytest

from core.config import settings
from core.repositories.device_fingerprint import record_second_device
from core.web_login import fingerprint as fp_module
from core.web_login.fingerprint import LocaleRejectedError
from schemas.accounts import AccountCreate
from schemas.device_fingerprint import DeviceFingerprint
from services.accounts import _web_identity, add_account
from services.accounts._web_identity import launch_dressed, web_fingerprint

if TYPE_CHECKING:
    from core.web_login.fingerprint import Fingerprint

_MEXICAN_PHONE = "+5215550001111"  # es-MX on whichever device it mints


@pytest.fixture(autouse=True)
def logged(monkeypatch: pytest.MonkeyPatch) -> list[tuple[str, str | None]]:
    """A pinned installed browser, a mobile-capable config and an empty refusal cache."""
    monkeypatch.setattr(fp_module, "_observed", {"build": "148.0.7778.217", "edge": ""})
    monkeypatch.setattr(settings.telegram, "mobile_api_id", 6)
    monkeypatch.setattr(settings.telegram, "mobile_api_hash", "mobile-hash")
    monkeypatch.setattr(_web_identity, "_rejected_locales", set())
    logged: list[tuple[str, str | None]] = []

    async def _log(_level: str, event: str, account_id: str | None = None, **_: Any) -> None:
        logged.append((event, account_id))

    monkeypatch.setattr(_web_identity, "log_event", _log)
    return logged


def _desktop_row(account_id: str, language: str) -> DeviceFingerprint:
    return DeviceFingerprint(
        account_id=account_id,
        device_type="desktop",
        platform="windows",
        device_model="Desktop",
        system_version="Windows 11",
        app_version="5.4.0 x64",
        lang_code=language.split("-", maxsplit=1)[0],
        system_lang_code=language,
    )


@pytest.mark.asyncio
async def test_an_account_with_both_devices_speaks_its_desktop_language() -> None:
    """The web window sits beside the desktop session, so that is the one it matches."""
    await add_account(
        AccountCreate(
            account_id="acc", session_name="acc", phone=_MEXICAN_PHONE, session_device="mobile"
        )
    )
    await record_second_device("acc_desktop", _desktop_row("acc", "ru-RU"))

    fingerprint = await web_fingerprint("acc", "DE")

    assert fingerprint.locale == "ru-RU"
    assert fingerprint.timezone == "Europe/Berlin"


@pytest.mark.asyncio
async def test_a_mobile_only_account_speaks_its_mobile_language() -> None:
    await add_account(
        AccountCreate(
            account_id="solo", session_name="solo", phone=_MEXICAN_PHONE, session_device="mobile"
        )
    )

    assert (await web_fingerprint("solo", "DE")).locale == "es-MX"


@pytest.mark.asyncio
async def test_a_locale_chrome_refuses_falls_back_to_the_proxy_language(
    logged: list[tuple[str, str | None]],
) -> None:
    """The window still opens; the refusal is logged by code, and not paid for twice."""
    await add_account(AccountCreate(account_id="acc", session_name="acc", session_device="mobile"))
    await record_second_device("acc_desktop", _desktop_row("acc", "bi-VU"))
    launched: list[Fingerprint] = []

    async def _launch(fingerprint: Fingerprint) -> Any:
        launched.append(fingerprint)
        if fingerprint.locale == "bi-VU":
            raise LocaleRejectedError(fingerprint.locale)
        return "window"

    assert await launch_dressed("acc", "DE", _launch) == "window"

    assert [fingerprint.locale for fingerprint in launched] == ["bi-VU", "de-DE"]
    assert launched[-1].timezone == "Europe/Berlin"
    assert logged == [("web_login_locale_rejected", "acc")]
    # The refusal belongs to this Chrome build: the next window starts in de-DE at once.
    assert (await web_fingerprint("acc", "DE")).locale == "de-DE"


@pytest.mark.asyncio
async def test_a_refused_proxy_language_is_not_retried_in_itself() -> None:
    async def _launch(fingerprint: Fingerprint) -> Any:
        raise LocaleRejectedError(fingerprint.locale)

    with pytest.raises(LocaleRejectedError):
        await launch_dressed("nobody", "DE", _launch)
