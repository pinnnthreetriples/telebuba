"""The Госуслуги app on a phone: its APK from RuStore, installed when the phone lacks it.

Госуслуги publishes no APK of its own; RuStore (the store it ships through, publisher
Минцифры) hands out its current build. RuStore's web API names that build's URL:
``overallInfo`` gives the app's id, ``v2/download-link`` the file (one universal APK,
``withoutSplits``). RuStore serves Russia: from abroad its CDN crawls, so the lookup and
the download both go through the phone's own proxy.

The file is kept only when signed with Госуслуги's certificate
(``GOSUSLUGI_CERTIFICATE_SHA256``, the one RuStore lists for it), and Android checks
the signature against that certificate on install. A file already in place is used as
it is (an APK the operator put there included), never updated behind their back.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

import httpx

from core.config import settings
from core.emulator import adb
from core.emulator._errors import EmulatorError
from core.emulator.assets import ensure_file, http_client, signed_by

if TYPE_CHECKING:
    from pathlib import Path

    from schemas.emulator import EmulatorRefusalCode
    from schemas.proxy import ProxySettings

_EMULATOR_DOWNLOAD_FAILED: EmulatorRefusalCode = "emulator_download_failed"

GOSUSLUGI_PACKAGE = "ru.rostel"
# Госуслуги's signing certificate, ``apksigner --print-certs``'s SHA-256.
GOSUSLUGI_CERTIFICATE_SHA256 = "c808c534ca149251b19d09bef9cd9558db5fa6e0ed1081835e273568b0aaa3fa"
_RUSTORE_API = "https://backapi.rustore.ru/applicationData"
# RuStore's API answers only a client that names its version.
_RUSTORE_HEADERS = {"ruStoreVerCode": "110802"}
# A sideloaded APK's installer, as when the operator installs a downloaded file.
_INSTALLER = "com.android.packageinstaller"
_LOOKUP_SECONDS = 30.0


async def ensure_gosuslugi(serial: str, proxy: ProxySettings) -> None:
    """Install Госуслуги on the phone when it lacks it, fetching the APK first."""
    if GOSUSLUGI_PACKAGE in await adb.installed_packages(serial):
        return
    apk = await ensure_gosuslugi_apk(proxy)
    await adb.install_apk(serial, apk, installer_package=_INSTALLER)


async def ensure_gosuslugi_apk(proxy: ProxySettings) -> Path:
    """The Госуслуги APK, downloaded from RuStore through ``proxy`` when missing."""
    target = settings.emulator.gosuslugi_apk_path

    async def apk_url() -> str:
        return await _rustore_apk_url(GOSUSLUGI_PACKAGE, proxy)

    await ensure_file(
        target,
        apk_url,
        signed_by(GOSUSLUGI_CERTIFICATE_SHA256),
        proxy=proxy,
        seconds=settings.emulator.app_download_timeout_seconds,
    )
    return target


async def _rustore_apk_url(package: str, proxy: ProxySettings) -> str:
    """The URL of ``package``'s current universal APK, as RuStore names it."""
    try:
        async with http_client(proxy, _LOOKUP_SECONDS) as client:
            info = await client.get(
                f"{_RUSTORE_API}/overallInfo/{package}", headers=_RUSTORE_HEADERS
            )
            info.raise_for_status()
            app_id = info.json()["body"]["appId"]
            link = await client.post(
                f"{_RUSTORE_API}/v2/download-link",
                headers=_RUSTORE_HEADERS,
                json={"appId": app_id, "firstInstall": True, "withoutSplits": True},
            )
            link.raise_for_status()
            url = link.json()["body"]["downloadUrls"][0]["url"]
    except (httpx.HTTPError, ValueError, KeyError, IndexError, TypeError) as exc:
        raise EmulatorError(_EMULATOR_DOWNLOAD_FAILED) from exc
    if not isinstance(url, str) or not url.startswith("https://"):
        raise EmulatorError(_EMULATOR_DOWNLOAD_FAILED)
    return url
