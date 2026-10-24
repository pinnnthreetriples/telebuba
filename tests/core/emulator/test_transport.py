"""The transport Android files eth0 under: a signed resource overlay, built in Python."""

from __future__ import annotations

import base64
import hashlib
import io
import struct
import zipfile
from typing import TYPE_CHECKING

from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.hazmat.primitives.serialization import pkcs7

from core.emulator import _transport
from tests.core.emulator.test_features import _EMU_MARKERS

if TYPE_CHECKING:
    from pathlib import Path

# NetworkCapabilities: INTERNET, NOT_RESTRICTED, TRUSTED, NOT_VPN, NOT_ROAMING,
# NOT_CONGESTED, NOT_SUSPENDED, NOT_VCN_MANAGED; Wi-Fi and Ethernet add NOT_METERED (11).
_CELLULAR = "eth0;12,13,14,15,18,20,21,28;;0"
_ETHERNET = "eth0;11,12,13,14,15,18,20,21,28;;3"


def _entries(apk: bytes) -> dict[str, bytes]:
    with zipfile.ZipFile(io.BytesIO(apk)) as archive:
        return {name: archive.read(name) for name in archive.namelist()}


def _pool_strings(chunk: bytes) -> list[str]:
    """The strings of a ``ResStringPool`` chunk, UTF-8 or UTF-16."""
    header_size, _, count, _, flags, start = struct.unpack_from("<HIIIII", chunk, 2)
    assert header_size == 28
    strings = []
    for index in range(count):
        at = start + struct.unpack_from("<I", chunk, 28 + 4 * index)[0]
        if flags & 0x100:
            size = chunk[at + 1]
            strings.append(chunk[at + 2 : at + 2 + size].decode())
        else:
            size = struct.unpack_from("<H", chunk, at)[0]
            strings.append(chunk[at + 2 : at + 2 + 2 * size].decode("utf-16-le"))
    return strings


def _table_value(arsc: bytes) -> str:
    """The one value of the overlay's resource table: its global string pool's only string."""
    assert struct.unpack_from("<HHI", arsc) == (0x0002, 12, len(arsc))
    (value,) = _pool_strings(arsc[12:])
    return value


def test_a_cellular_phone_files_eth0_as_a_metered_mobile_network() -> None:
    entries = _entries(_transport.overlay_apk("cellular"))

    assert _table_value(entries["resources.arsc"]) == _CELLULAR, "TRANSPORT_CELLULAR, metered"


def test_ethernet_keeps_aosps_own_capabilities() -> None:
    entries = _entries(_transport.overlay_apk("ethernet"))

    assert _table_value(entries["resources.arsc"]) == _ETHERNET, "what EthernetTracker defaults to"


def test_the_table_overrides_the_ethernet_interfaces_array() -> None:
    arsc = _entries(_transport.overlay_apk("cellular"))["resources.arsc"]

    assert b"config_ethernet_interfaces" in arsc
    assert "array".encode("utf-16-le") in arsc or b"array" in arsc


def test_the_manifest_is_a_static_overlay_of_the_connectivity_resources() -> None:
    manifest = _entries(_transport.overlay_apk("cellular"))["AndroidManifest.xml"]

    assert struct.unpack_from("<HHI", manifest) == (0x0003, 8, len(manifest))
    strings = _pool_strings(manifest[8:])
    for expected in (
        "com.android.connectivity.resources",
        "ServiceConnectivityResourcesConfig",
        "overlay",
        "isStatic",
        "targetName",
        _transport.OVERLAY_PACKAGE,
    ):
        assert expected in strings
    ids = struct.pack("<I", 0x0101055A) + struct.pack("<I", 0x0101044D)  # isStatic, targetName
    assert all(attr_id in manifest for attr_id in (ids[:4], ids[4:]))


def test_the_table_is_stored_and_aligned_first() -> None:
    with zipfile.ZipFile(io.BytesIO(_transport.overlay_apk("cellular"))) as archive:
        first = archive.infolist()[0]
        assert first.filename == "resources.arsc"
        assert first.compress_type == zipfile.ZIP_STORED
        data_offset = first.header_offset + 30 + len(first.filename) + len(first.extra)
        assert data_offset % 4 == 0, "Android maps it in place"


def test_the_same_transport_gives_the_same_bytes() -> None:
    assert _transport.overlay_apk("cellular") == _transport.overlay_apk("cellular")
    assert _transport.overlay_apk("cellular") != _transport.overlay_apk("ethernet")


def test_the_jar_signature_covers_every_entry() -> None:
    entries = _entries(_transport.overlay_apk("cellular"))
    manifest = entries["META-INF/MANIFEST.MF"].decode()
    signature_file = entries["META-INF/CERT.SF"]

    for name in ("resources.arsc", "AndroidManifest.xml"):
        digest = base64.b64encode(hashlib.sha256(entries[name]).digest()).decode()
        assert f"Name: {name}\r\nSHA-256-Digest: {digest}\r\n" in manifest
    whole = base64.b64encode(hashlib.sha256(entries["META-INF/MANIFEST.MF"]).digest()).decode()
    assert f"SHA-256-Digest-Manifest: {whole}\r\n".encode() in signature_file
    assert b"X-Android-APK-Signed" not in signature_file, "v1 only: no v2 block is claimed"

    block = entries["META-INF/CERT.EC"]
    (certificate,) = pkcs7.load_der_pkcs7_certificates(block)
    public_key = certificate.public_key()
    assert isinstance(public_key, ec.EllipticCurvePublicKey)
    size = next(n for n in range(72, 7, -1) if block[-n - 2 : -n] == bytes((0x04, n)))
    public_key.verify(block[-size:], signature_file, ec.ECDSA(hashes.SHA256()))


def test_nothing_the_overlay_names_is_an_emulators() -> None:
    """Its own names and values; the manifest's schema (``uses-sdk``) is every APK's."""
    entries = _entries(_transport.overlay_apk("cellular"))
    (certificate,) = pkcs7.load_der_pkcs7_certificates(entries["META-INF/CERT.EC"])
    own = [
        _transport.OVERLAY_PACKAGE,
        _transport.OVERLAY_DIR,
        certificate.subject.rfc4514_string(),
        *entries,
        *_pool_strings(entries["resources.arsc"][12:]),
    ]
    for text in own:
        assert not [m for m in _EMU_MARKERS if m in text.lower()], text


def test_the_tree_lands_where_the_package_manager_scans(tmp_path: Path) -> None:
    _transport.write_overlay_tree(tmp_path, "cellular")

    (apk,) = (tmp_path / "overlay").rglob("*.apk")
    assert apk.relative_to(tmp_path).parts == (
        "overlay",
        _transport.OVERLAY_DIR,
        f"{_transport.OVERLAY_DIR}.apk",
    )
    assert apk.read_bytes() == _transport.overlay_apk("cellular")
