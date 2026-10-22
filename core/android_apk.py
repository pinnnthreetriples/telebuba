"""Read an APK on the host: who signed it, and the app it holds.

Standard library only. ``signing_certificate_sha256`` reads the APK Signing Block
and returns the SHA-256 of its signer's certificate, the fingerprint ``apksigner
verify --print-certs`` prints, only when every signer of every v2, v3 and v3.1 block
carries that one certificate. Nothing here verifies the signature itself: Android
does that on install, but checks only one block (v3.1, else v3, else v2) and in v3
only the signer for its SDK; so a second certificate anywhere could be the one
Android installs, and an APK with two is refused. One certificate plus Android's own
check means the file is the signer's. ``app_identity`` reads the binary ``AndroidManifest.xml``:
the package, and ``versionName (versionCode)``, the version the app sends Telegram.

Both raise ``ValueError`` on a file that is not an APK of that shape;
``local_apk_identity`` is ``app_identity`` of the configured Telegram APK, ``None``
when there is none (or it cannot be read), cached per file version. Kept out of
``core.emulator`` so the fingerprint catalogue can read it without the container
layer.
"""

from __future__ import annotations

import hashlib
import struct
import zipfile
from functools import lru_cache
from pathlib import Path
from typing import NamedTuple

from core.config import settings

_SIG_BLOCK_MAGIC = b"APK Sig Block 42"
_EOCD_MAGIC = b"PK\x05\x06"
_V2_ID = 0x7109871A
_V3_ID = 0xF05368C0
_V31_ID = 0x1B93AD61

# Binary XML chunk types and the android: attribute ids of the manifest's version.
_STRING_POOL = 0x0001
_RESOURCE_MAP = 0x0180
_START_ELEMENT = 0x0102
_UTF8_FLAG = 0x100
_TYPE_STRING = 0x03
_TYPE_INT_DEC = 0x10
_TYPE_INT_HEX = 0x11
_VERSION_CODE_ID = 0x0101021B
_VERSION_NAME_ID = 0x0101021C
_NO_STRING = 0xFFFFFFFF


class AppIdentity(NamedTuple):
    package: str
    version: str  # "versionName (versionCode)"


class ApkStoreEntry(NamedTuple):
    package: str
    version: str  # "versionName (versionCode)"
    path: Path


def signing_certificate_sha256(path: Path) -> str:
    """The SHA-256 (hex) of the one certificate every v2/v3/v3.1 signer of the APK uses."""
    data = path.read_bytes()
    try:
        blocks = _signing_blocks(data)
        found = {
            hashlib.sha256(certificate).hexdigest()
            for block_id in (_V2_ID, _V3_ID, _V31_ID)
            if block_id in blocks
            for certificate in _signer_certificates(blocks[block_id])
        }
    except (struct.error, IndexError) as exc:
        msg = "truncated signing block"
        raise ValueError(msg) from exc
    if not found:
        msg = "no v2/v3 signature"
        raise ValueError(msg)
    if len(found) > 1:
        msg = "more than one signing certificate"
        raise ValueError(msg)
    return found.pop()


def app_identity(path: Path) -> AppIdentity:
    """The package and ``versionName (versionCode)`` the APK's manifest declares."""
    try:
        with zipfile.ZipFile(path) as apk:
            manifest = apk.read("AndroidManifest.xml")
    except (zipfile.BadZipFile, KeyError) as exc:
        msg = "no manifest"
        raise ValueError(msg) from exc
    try:
        attributes = _manifest_attributes(manifest)
    except (struct.error, IndexError) as exc:
        msg = "truncated manifest"
        raise ValueError(msg) from exc
    package = attributes.get("package")
    name = attributes.get(_VERSION_NAME_ID)
    code = attributes.get(_VERSION_CODE_ID)
    if not package or not name or not code:
        msg = "manifest without package or version"
        raise ValueError(msg)
    return AppIdentity(package, f"{name} ({code})")


def local_apk_identity() -> AppIdentity | None:
    """The configured Telegram APK's identity; ``None`` without a readable one."""
    path = settings.emulator.telegram_apk_path
    try:
        stat = path.stat()
    except OSError:
        return None
    return _cached_identity(str(path), stat.st_mtime_ns, stat.st_size)


@lru_cache(maxsize=4)
def _cached_identity(path: str, _mtime_ns: int, _size: int) -> AppIdentity | None:
    """Keyed by the file's version too, so a replaced APK is read again."""
    try:
        return app_identity(Path(path))
    except (ValueError, OSError):
        return None


def list_store_apks(store_dir: Path | None = None) -> list[ApkStoreEntry]:
    """List valid APKs found in the local versioned store (<store_dir>/**/*.apk)."""
    root = store_dir or settings.emulator.apk_store_dir
    if not root.is_dir():
        return []
    entries: list[ApkStoreEntry] = []
    for file in sorted(root.glob("**/*.apk")):
        if not file.is_file():
            continue
        try:
            identity = app_identity(file)
            entries.append(
                ApkStoreEntry(package=identity.package, version=identity.version, path=file)
            )
        except (ValueError, OSError):
            continue
    return entries


def find_store_apk(
    package: str,
    version: str | None = None,
    *,
    store_dir: Path | None = None,
) -> Path | None:
    """Find an APK in the local store matching package and optional version."""
    for entry in list_store_apks(store_dir):
        if entry.package == package and (version is None or entry.version == version):
            return entry.path
    return None


def _signing_blocks(data: bytes) -> dict[int, bytes]:
    eocd = data.rfind(_EOCD_MAGIC)
    if eocd < 0:
        msg = "not a zip"
        raise ValueError(msg)
    directory = struct.unpack_from("<I", data, eocd + 16)[0]
    if data[directory - 16 : directory] != _SIG_BLOCK_MAGIC:
        msg = "no signing block"
        raise ValueError(msg)
    size = struct.unpack_from("<Q", data, directory - 24)[0]
    position, end = directory - size, directory - 24
    blocks: dict[int, bytes] = {}
    while position < end:
        length, block_id = struct.unpack_from("<QI", data, position)
        blocks[block_id] = data[position + 12 : position + 8 + length]
        position += 8 + length
    return blocks


def _prefixed(buffer: bytes, position: int) -> tuple[bytes, int]:
    """One uint32-length-prefixed field at ``position``, and where the next one starts."""
    length = struct.unpack_from("<I", buffer, position)[0]
    end = position + 4 + length
    if end > len(buffer):
        msg = "truncated signing block"
        raise ValueError(msg)
    return buffer[position + 4 : end], end


def _signer_certificates(block: bytes) -> list[bytes]:
    """Each signer's own (first) certificate in one v2/v3 block."""
    signers, _ = _prefixed(block, 0)
    found: list[bytes] = []
    position = 0
    while position < len(signers):
        signer, position = _prefixed(signers, position)
        signed_data, _ = _prefixed(signer, 0)
        _digests, at = _prefixed(signed_data, 0)
        certificates, _ = _prefixed(signed_data, at)
        certificate, _ = _prefixed(certificates, 0)
        found.append(certificate)
    return found


def _strings(chunk: bytes) -> list[str]:
    count, _styles, flags, start = struct.unpack_from("<IIII", chunk, 8)
    offsets = struct.unpack_from(f"<{count}I", chunk, 28)
    utf8 = bool(flags & _UTF8_FLAG)
    return [_string_at(chunk, start + offset, utf8=utf8) for offset in offsets]


def _string_at(chunk: bytes, position: int, *, utf8: bool) -> str:
    if utf8:
        position += 2 if chunk[position] & 0x80 else 1  # length in characters
        length = chunk[position]
        if length & 0x80:
            length = ((length & 0x7F) << 8) | chunk[position + 1]
            position += 1
        return chunk[position + 1 : position + 1 + length].decode("utf-8")
    length = struct.unpack_from("<H", chunk, position)[0]
    if length & 0x8000:
        length = ((length & 0x7FFF) << 16) | struct.unpack_from("<H", chunk, position + 2)[0]
        position += 2
    return chunk[position + 2 : position + 2 + length * 2].decode("utf-16-le")


def _manifest_attributes(xml: bytes) -> dict[str | int, str]:
    """The ``<manifest>`` element's attributes: android ones by resource id, others by name."""
    strings: list[str] = []
    resource_ids: tuple[int, ...] = ()
    position = struct.unpack_from("<H", xml, 2)[0]
    while position < len(xml):
        kind, _header, size = struct.unpack_from("<HHI", xml, position)
        chunk = xml[position : position + size]
        if kind == _STRING_POOL:
            strings = _strings(chunk)
        elif kind == _RESOURCE_MAP:
            resource_ids = struct.unpack_from(f"<{(size - 8) // 4}I", chunk, 8)
        elif kind == _START_ELEMENT:
            return _element_attributes(chunk, strings, resource_ids)
        if size <= 0:
            break
        position += size
    msg = "no manifest element"
    raise ValueError(msg)


def _element_attributes(
    chunk: bytes, strings: list[str], resource_ids: tuple[int, ...]
) -> dict[str | int, str]:
    start, size, count = struct.unpack_from("<HHH", chunk, 24)
    attributes: dict[str | int, str] = {}
    for index in range(count):
        offset = 16 + start + index * size
        _ns, name, raw, _vsize, _res, kind, value = struct.unpack_from("<IIIHBBI", chunk, offset)
        key: str | int = resource_ids[name] if name < len(resource_ids) else strings[name]
        if raw != _NO_STRING:
            attributes[key] = strings[raw]
        elif kind == _TYPE_STRING:
            attributes[key] = strings[value]
        elif kind in {_TYPE_INT_DEC, _TYPE_INT_HEX}:
            attributes[key] = str(value)
    return attributes
