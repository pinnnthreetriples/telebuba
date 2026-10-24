"""The transport Android files the pod's eth0 under: cellular, not Ethernet.

redroid's only interface is eth0, which AOSP's ``EthernetTracker`` publishes as
``TRANSPORT_ETHERNET``: ``ConnectivityManager`` and the WebView's
``navigator.connection`` then tell an app the phone is on a wired network. The
tracker reads ``config_ethernet_interfaces`` from the connectivity module's resources
(``com.android.connectivity.resources``), and an entry may name the transport the
interface is published with (``<iface>;<capabilities>;<ip config>;<transport>``,
``EthernetTracker.createNetworkCapabilities``). That array is overlayable by
vendor and product overlays, so a static resource overlay in ``/product/overlay``
changes the label and nothing else: the kernel, the interface, its address and the
gateway's kill-switch are untouched. No kernel module (mac80211_hwsim, virt_wifi)
is involved; those are host-wide.

``cellular`` matches the phone's SIM and LTE properties (``core.emulator._profile``),
metered as a mobile network is; ``ethernet`` is AOSP's own default, kept so a
refresh can turn the label back. Wi-Fi is not offered: WifiManager would still say
Wi-Fi is off. The empty IP config keeps the static address redroid stores for eth0.

The APK is built here, with no Android SDK: a binary manifest, a one-entry
resource table and a JAR (v1) signature. PackageManager refuses an unsigned
package even on /product, but trusts any certificate there, so the key is derived
from a fixed label and signing is deterministic: the same transport always gives
the same bytes, so PackageManager sees the same signer at every refresh.
"""

from __future__ import annotations

import base64
import hashlib
import io
import struct
import zipfile
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Literal

from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.x509.oid import NameOID

if TYPE_CHECKING:
    from pathlib import Path

Transport = Literal["cellular", "ethernet"]

OVERLAY_PACKAGE = "com.android.connectivity.resources.product"
OVERLAY_DIR = "ConnectivityResourcesProduct"
_TARGET_PACKAGE = "com.android.connectivity.resources"
_TARGET_NAME = "ServiceConnectivityResourcesConfig"
_ARRAY = "config_ethernet_interfaces"
# NetworkCapabilities.NET_CAPABILITY_*: INTERNET, NOT_RESTRICTED, TRUSTED, NOT_VPN,
# NOT_ROAMING, NOT_CONGESTED, NOT_SUSPENDED, NOT_VCN_MANAGED; NOT_METERED (11) for
# Ethernet only. NetworkCapabilities.TRANSPORT_CELLULAR is 0, TRANSPORT_ETHERNET 3.
_ENTRIES: dict[Transport, str] = {
    "cellular": "eth0;12,13,14,15,18,20,21,28;;0",
    "ethernet": "eth0;11,12,13,14,15,18,20,21,28;;3",
}
# The SDK the manifest declares: below 30, a v1 signature is all PackageManager wants.
_SDK = 29

# Binary XML and resource table chunk types (frameworks/base libs/androidfw ResourceTypes.h).
_RES_STRING_POOL = 0x0001
_RES_TABLE = 0x0002
_RES_XML = 0x0003
_XML_START_NAMESPACE = 0x0100
_XML_END_NAMESPACE = 0x0101
_XML_START_ELEMENT = 0x0102
_XML_END_ELEMENT = 0x0103
_XML_RESOURCE_MAP = 0x0180
_TABLE_PACKAGE = 0x0200
_TABLE_TYPE = 0x0201
_TABLE_TYPE_SPEC = 0x0202
_UTF8_FLAG = 0x100
# The longest string a one-byte UTF-8 pool length holds; DER's short-form length too.
_SHORT_LENGTH = 0x7F
_NO_INDEX = 0xFFFFFFFF
_TYPE_STRING = 0x03
_TYPE_INT_DEC = 0x10
_TYPE_INT_BOOLEAN = 0x12
_ENTRY_COMPLEX = 0x0001
# An array's first item: ResTable_map::ATTR_MIN, as aapt2 writes it.
_ARRAY_ITEM = 0x01000001
_ANDROID_URI = "http://schemas.android.com/apk/res/android"
# android:* attributes the manifest uses, by resource id (sorted: the parser looks them up so).
_ATTRS = {
    "minSdkVersion": 0x0101020C,
    "targetSdkVersion": 0x01010270,
    "hasCode": 0x0101000C,
    "priority": 0x0101001C,
    "targetPackage": 0x01010021,
    "targetName": 0x0101044D,
    "isStatic": 0x0101055A,
}

_ZIP_TIME = (2009, 1, 1, 0, 0, 0)
_SIGNER = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "Android")])
_KEY_LABEL = b"product overlay signer"
_SIGNED_DATA = "1.2.840.113549.1.7.2"
_DATA = "1.2.840.113549.1.7.1"
_SHA256 = "2.16.840.1.101.3.4.2.1"
_ECDSA_SHA256 = "1.2.840.10045.4.3.2"


def overlay_apk(transport: Transport) -> bytes:
    """The signed overlay that publishes eth0 as ``transport``."""
    entries = {
        "resources.arsc": _resource_table(_ENTRIES[transport]),
        "AndroidManifest.xml": _manifest(),
    }
    return _signed_zip(entries)


def write_overlay_tree(directory: Path, transport: Transport) -> None:
    """``overlay/<dir>/<dir>.apk`` under ``directory``, for one ``docker cp`` into /product."""
    target = directory / "overlay" / OVERLAY_DIR
    target.mkdir(parents=True, exist_ok=True)
    (target / f"{OVERLAY_DIR}.apk").write_bytes(overlay_apk(transport))


def _chunk(kind: int, header: bytes, body: bytes) -> bytes:
    """A ResChunk: type, header size, total size, the rest of its header, its body."""
    return struct.pack("<HHI", kind, 8 + len(header), 8 + len(header) + len(body)) + header + body


def _pad(data: bytes) -> bytes:
    return data + b"\0" * (-len(data) % 4)


def _string_pool(strings: list[str], *, utf8: bool) -> bytes:
    data = b""
    offsets = []
    for text in strings:
        offsets.append(len(data))
        if utf8:
            encoded = text.encode()
            if max(len(text), len(encoded)) > _SHORT_LENGTH:
                raise ValueError(text)
            data += bytes((len(text), len(encoded))) + encoded + b"\0"
        else:
            data += struct.pack("<H", len(text)) + text.encode("utf-16-le") + b"\0\0"
    start = 28 + 4 * len(strings)
    header = struct.pack(
        "<IIIII", len(strings), 0, _UTF8_FLAG if utf8 else 0, start if strings else 0, 0
    )
    return _chunk(_RES_STRING_POOL, header, struct.pack(f"<{len(offsets)}I", *offsets) + _pad(data))


def _resource_table(value: str) -> bytes:
    """``resources.arsc`` with one string-array, ``_ARRAY``, holding ``value``."""
    type_strings = _string_pool(["array"], utf8=False)
    key_strings = _string_pool([_ARRAY], utf8=True)
    spec = _chunk(_TABLE_TYPE_SPEC, struct.pack("<BBHI", 1, 0, 0, 1), struct.pack("<I", 0))
    # One complex entry (key 0, no parent, one item) whose item is global string 0.
    entry = struct.pack("<HHIII", 16, _ENTRY_COMPLEX, 0, 0, 1)
    item = struct.pack("<IHBBI", _ARRAY_ITEM, 8, 0, _TYPE_STRING, 0)
    config = struct.pack("<I", 64) + b"\0" * 60
    type_header = struct.pack("<BBHII", 1, 0, 0, 1, 84 + 4) + config
    table_type = _chunk(_TABLE_TYPE, type_header, struct.pack("<I", 0) + entry + item)
    name = OVERLAY_PACKAGE.encode("utf-16-le").ljust(256, b"\0")
    package_header = struct.pack("<I", 0x7F) + name
    package_header += struct.pack("<IIIII", 288, 0, 288 + len(type_strings), 0, 0)
    package = _chunk(_TABLE_PACKAGE, package_header, type_strings + key_strings + spec + table_type)
    return _chunk(_RES_TABLE, struct.pack("<I", 1), _string_pool([value], utf8=True) + package)


def _manifest() -> bytes:
    """The binary ``AndroidManifest.xml`` of a static overlay of the connectivity resources."""
    strings = [*_ATTRS, "android", _ANDROID_URI, "manifest", "package", OVERLAY_PACKAGE]
    strings += ["uses-sdk", "application", "overlay", _TARGET_PACKAGE, _TARGET_NAME]
    index = {text: position for position, text in enumerate(strings)}
    uri = index[_ANDROID_URI]

    def node(kind: int, extension: bytes) -> bytes:
        return _chunk(kind, struct.pack("<II", 1, _NO_INDEX), extension)

    def attribute(name: str, kind: int, data: int, *, raw: str | None = None) -> bytes:
        namespace = uri if name in _ATTRS else _NO_INDEX
        raw_index = index[raw] if raw is not None else _NO_INDEX
        return struct.pack("<IIIHBBI", namespace, index[name], raw_index, 8, 0, kind, data)

    def element(name: str, attributes: list[bytes], children: bytes = b"") -> bytes:
        start = struct.pack("<IIHHHHHH", _NO_INDEX, index[name], 20, 20, len(attributes), 0, 0, 0)
        end = struct.pack("<II", _NO_INDEX, index[name])
        return (
            node(_XML_START_ELEMENT, start + b"".join(attributes))
            + children
            + node(_XML_END_ELEMENT, end)
        )

    def string(name: str, value: str) -> bytes:
        return attribute(name, _TYPE_STRING, index[value], raw=value)

    children = element(
        "uses-sdk",
        [
            attribute("minSdkVersion", _TYPE_INT_DEC, _SDK),
            attribute("targetSdkVersion", _TYPE_INT_DEC, _SDK),
        ],
    )
    children += element("application", [attribute("hasCode", _TYPE_INT_BOOLEAN, 0)])
    children += element(
        "overlay",
        [
            attribute("priority", _TYPE_INT_DEC, 1),
            string("targetPackage", _TARGET_PACKAGE),
            string("targetName", _TARGET_NAME),
            attribute("isStatic", _TYPE_INT_BOOLEAN, _NO_INDEX),
        ],
    )
    namespace = struct.pack("<II", index["android"], uri)
    body = (
        _string_pool(strings, utf8=False)
        + _chunk(_XML_RESOURCE_MAP, b"", struct.pack(f"<{len(_ATTRS)}I", *_ATTRS.values()))
        + node(_XML_START_NAMESPACE, namespace)
        + element("manifest", [string("package", OVERLAY_PACKAGE)], children)
        + node(_XML_END_NAMESPACE, namespace)
    )
    return _chunk(_RES_XML, b"", body)


def _signed_zip(entries: dict[str, bytes]) -> bytes:
    """The APK: ``entries`` stored (the table first, so 4-byte aligned) and JAR-signed."""
    main = "Manifest-Version: 1.0\r\nCreated-By: 1.0 (Android)\r\n\r\n"
    sections = [
        f"Name: {name}\r\nSHA-256-Digest: {_digest(data)}\r\n\r\n" for name, data in entries.items()
    ]
    manifest = (main + "".join(sections)).encode()
    signature_file = (
        "Signature-Version: 1.0\r\nCreated-By: 1.0 (Android)\r\n"
        f"SHA-256-Digest-Manifest: {_digest(manifest)}\r\n\r\n"
    )
    for name, section in zip(entries, sections, strict=True):
        signature_file += f"Name: {name}\r\nSHA-256-Digest: {_digest(section.encode())}\r\n\r\n"
    files = {
        **entries,
        "META-INF/MANIFEST.MF": manifest,
        "META-INF/CERT.SF": signature_file.encode(),
        "META-INF/CERT.EC": _signature_block(signature_file.encode()),
    }
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", zipfile.ZIP_STORED) as archive:
        for name, data in files.items():
            archive.writestr(zipfile.ZipInfo(name, _ZIP_TIME), data)
    return buffer.getvalue()


def _digest(data: bytes) -> str:
    return base64.b64encode(hashlib.sha256(data).digest()).decode()


def _signing_key() -> ec.EllipticCurvePrivateKey:
    secret = int.from_bytes(hashlib.sha256(_KEY_LABEL).digest(), "big")
    return ec.derive_private_key(secret, ec.SECP256R1())


def _certificate(key: ec.EllipticCurvePrivateKey) -> x509.Certificate:
    return (
        x509.CertificateBuilder()
        .subject_name(_SIGNER)
        .issuer_name(_SIGNER)
        .public_key(key.public_key())
        .serial_number(1)
        .not_valid_before(datetime(2008, 1, 1, tzinfo=UTC))
        .not_valid_after(datetime(2048, 1, 1, tzinfo=UTC))
        .sign(key, hashes.SHA256(), ecdsa_deterministic=True)
    )


def _signature_block(signature_file: bytes) -> bytes:
    """``CERT.EC``: a PKCS#7 SignedData, detached, over ``signature_file`` (no attributes)."""
    key = _signing_key()
    certificate = _certificate(key)
    signature = key.sign(signature_file, ec.ECDSA(hashes.SHA256(), deterministic_signing=True))
    sha256 = _der(0x30, _oid(_SHA256) + _der(0x05, b""))
    signer = _der(
        0x30,
        _integer(1)
        + _der(0x30, certificate.issuer.public_bytes() + _integer(certificate.serial_number))
        + sha256
        + _der(0x30, _oid(_ECDSA_SHA256))
        + _der(0x04, signature),
    )
    signed_data = _der(
        0x30,
        _integer(1)
        + _der(0x31, sha256)
        + _der(0x30, _oid(_DATA))
        + _der(0xA0, certificate.public_bytes(serialization.Encoding.DER))
        + _der(0x31, signer),
    )
    return _der(0x30, _oid(_SIGNED_DATA) + _der(0xA0, signed_data))


def _der(tag: int, content: bytes) -> bytes:
    size = len(content)
    if size <= _SHORT_LENGTH:
        return bytes((tag, size)) + content
    length = size.to_bytes((size.bit_length() + 7) // 8, "big")
    return bytes((tag, 0x80 | len(length))) + length + content


def _integer(value: int) -> bytes:
    return _der(0x02, value.to_bytes(value.bit_length() // 8 + 1, "big"))


def _oid(dotted: str) -> bytes:
    first, second, *rest = (int(part) for part in dotted.split("."))
    encoded = bytes((40 * first + second,))
    for arc in rest:
        septets = [arc & 0x7F]
        while arc := arc >> 7:
            septets.append(0x80 | (arc & 0x7F))
        encoded += bytes(reversed(septets))
    return _der(0x06, encoded)
