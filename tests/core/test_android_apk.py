"""An APK on the host: its signer's certificate and the app its manifest declares."""

from __future__ import annotations

import hashlib
import zipfile
from typing import TYPE_CHECKING

import pytest

from core import android_apk
from core.android_apk import AppIdentity, app_identity, signing_certificate_sha256
from core.config import settings
from core.device_fingerprint_android import ANDROID_APP_VERSIONS, android_app_version
from tests.core._apk_builder import V2_ID, V3_ID, V31_ID, build_apk

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


@pytest.mark.parametrize(
    "blocks",
    [
        {V2_ID: [_CERTIFICATE], V3_ID: [b"another"]},
        {V2_ID: [_CERTIFICATE], V31_ID: [b"another"]},
        {V3_ID: [_CERTIFICATE, b"another"]},
    ],
    ids=["v3-differs", "v3.1-differs", "second-v3-signer"],
)
def test_a_second_certificate_anywhere_is_refused(
    tmp_path: Path, blocks: dict[int, list[bytes]]
) -> None:
    """Android installs by one block (v3.1, else v3, else v2) and one v3 signer per SDK."""
    path = tmp_path / "two-signers.apk"
    build_apk(path, certificate=_CERTIFICATE, package="p", version=("1", 1), blocks=blocks)

    with pytest.raises(ValueError, match="more than one signing certificate"):
        signing_certificate_sha256(path)


def test_the_same_certificate_in_every_block_is_the_signer(tmp_path: Path) -> None:
    path = tmp_path / "v2-v3.apk"
    blocks = {V2_ID: [_CERTIFICATE], V3_ID: [_CERTIFICATE], V31_ID: [_CERTIFICATE]}
    build_apk(path, certificate=_CERTIFICATE, package="p", version=("1", 1), blocks=blocks)

    assert signing_certificate_sha256(path) == hashlib.sha256(_CERTIFICATE).hexdigest()


def test_a_cut_signing_block_is_refused_as_no_apk(apk: Path) -> None:
    data = apk.read_bytes()
    directory = data.index(b"APK Sig Block 42") + 16
    cut = apk.with_name("cut.apk")
    # The signing block's own size now points past the file's start.
    huge = (1 << 40).to_bytes(8, "little")
    cut.write_bytes(data[: directory - 24] + huge + data[directory - 16 :])

    with pytest.raises(ValueError, match="truncated signing block"):
        signing_certificate_sha256(cut)


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


def test_list_and_find_store_apks(tmp_path: Path) -> None:
    store = tmp_path / "apk_store"
    pkg_dir = store / "org.telegram.messenger"
    pkg_dir.mkdir(parents=True)
    apk1 = pkg_dir / "v1.apk"
    build_apk(
        apk1,
        certificate=_CERTIFICATE,
        package="org.telegram.messenger",
        version=("10.0.0", 100),
    )
    apk2 = pkg_dir / "v2.apk"
    build_apk(
        apk2,
        certificate=_CERTIFICATE,
        package="org.telegram.messenger",
        version=("11.0.0", 200),
    )

    entries = android_apk.list_store_apks(store)
    assert len(entries) == 2
    assert entries[0].package == "org.telegram.messenger"
    assert entries[0].version == "10.0.0 (100)"
    assert entries[1].version == "11.0.0 (200)"

    found_v1 = android_apk.find_store_apk("org.telegram.messenger", "10.0.0 (100)", store_dir=store)
    assert found_v1 == apk1
    found_any = android_apk.find_store_apk("org.telegram.messenger", store_dir=store)
    assert found_any == apk2
    assert android_apk.find_store_apk("unknown.pkg", store_dir=store) is None


def test_corrupted_or_truncated_manifest_is_refused(tmp_path: Path) -> None:
    bad = tmp_path / "corrupted.apk"
    with zipfile.ZipFile(bad, "w") as zf:
        zf.writestr("AndroidManifest.xml", b"\x00\x01\x02\x03")
    with pytest.raises(ValueError, match=r"(truncated manifest|no manifest)"):
        app_identity(bad)
