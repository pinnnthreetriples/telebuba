"""An APK on the host: its signer's certificate and the app its manifest declares."""

from __future__ import annotations

import hashlib
from typing import TYPE_CHECKING

import pytest

from core import android_apk
from core.android_apk import AppIdentity, app_identity, signing_certificate_sha256
from core.config import settings
from core.device_fingerprint_android import ANDROID_APP_VERSIONS, android_app_version
from tests.core._apk_builder import build_apk

if TYPE_CHECKING:
    from pathlib import Path

_CERTIFICATE = b"a DER certificate"


@pytest.fixture
def apk(tmp_path: Path) -> Path:
    path = tmp_path / "telegram.apk"
    build_apk(
        path,
        certificate=_CERTIFICATE,
        package="org.telegram.messenger.web",
        version=("13.0.3", 71839),
    )
    return path


def test_the_signer_and_the_app_are_read_off_the_file(apk: Path) -> None:
    assert signing_certificate_sha256(apk) == hashlib.sha256(_CERTIFICATE).hexdigest()
    assert app_identity(apk) == AppIdentity("org.telegram.messenger.web", "13.0.3 (71839)")


def test_a_file_that_is_no_apk_is_refused(tmp_path: Path) -> None:
    junk = tmp_path / "junk.apk"
    junk.write_bytes(b"not a zip at all")

    with pytest.raises(ValueError, match="not a zip"):
        signing_certificate_sha256(junk)
    with pytest.raises(ValueError, match="no manifest"):
        app_identity(junk)


def test_new_fingerprints_claim_the_apks_own_version(
    apk: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(settings.emulator, "telegram_apk_path", apk)
    monkeypatch.setattr(settings.emulator, "telegram_app_version", "")
    android_apk._cached_identity.cache_clear()

    assert android_app_version() == "13.0.3 (71839)"

    monkeypatch.setattr(settings.emulator, "telegram_app_version", "12.10.6 (7112)")
    assert android_app_version() == "12.10.6 (7112)", "the operator's setting wins"


def test_without_an_apk_the_version_comes_from_the_catalogue(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(settings.emulator, "telegram_apk_path", tmp_path / "none.apk")
    monkeypatch.setattr(settings.emulator, "telegram_app_version", "")

    assert android_apk.local_apk_identity() is None
    assert android_app_version() in ANDROID_APP_VERSIONS
