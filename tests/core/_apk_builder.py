"""Build a minimal APK for tests: a binary manifest and a v2 signing block.

Only the shape ``core.android_apk`` reads: the zip's central directory preceded by an
APK Signing Block whose v2 signer carries ``certificate``, and an
``AndroidManifest.xml`` whose ``<manifest>`` holds ``package``, ``versionCode``
(typed int) and ``versionName`` (raw string), the version attributes named through
the resource map as aapt2 writes them.
"""

from __future__ import annotations

import io
import struct
import zipfile
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from pathlib import Path

_V2_ID = 0x7109871A
_NO_STRING = 0xFFFFFFFF


def _prefixed(*parts: bytes) -> bytes:
    body = b"".join(parts)
    return struct.pack("<I", len(body)) + body


def _string_pool(strings: list[str]) -> bytes:
    encoded = [struct.pack("<H", len(s)) + s.encode("utf-16-le") + b"\0\0" for s in strings]
    offsets, position = [], 0
    for item in encoded:
        offsets.append(position)
        position += len(item)
    header_size = 28
    start = header_size + 4 * len(strings)
    body = struct.pack(f"<{len(offsets)}I", *offsets) + b"".join(encoded)
    body += b"\0" * (-len(body) % 4)
    head = struct.pack(
        "<HHIIIIII", 0x0001, header_size, header_size + len(body), len(strings), 0, 0, start, 0
    )
    return head + body


def manifest(package: str, version_name: str, version_code: int) -> bytes:
    """``<manifest package=… android:versionCode=… android:versionName=…>`` as binary XML."""
    strings = ["versionCode", "versionName", "package", "manifest", package, version_name]
    resource_map = struct.pack("<HHI", 0x0180, 8, 16) + struct.pack("<II", 0x0101021B, 0x0101021C)
    attributes = [
        struct.pack("<IIIHBBI", _NO_STRING, 0, _NO_STRING, 8, 0, 0x10, version_code),
        struct.pack("<IIIHBBI", _NO_STRING, 1, 5, 8, 0, 0x03, 5),
        struct.pack("<IIIHBBI", _NO_STRING, 2, 4, 8, 0, 0x03, 4),
    ]
    body = struct.pack("<II", 1, _NO_STRING) + struct.pack(
        "<IIHHHHHH", _NO_STRING, 3, 20, 20, len(attributes), 0, 0, 0
    )
    element = b"".join(attributes)
    start = struct.pack("<HHI", 0x0102, 16, 8 + len(body) + len(element)) + body + element
    chunks = _string_pool(strings) + resource_map + start
    return struct.pack("<HHI", 0x0003, 8, 8 + len(chunks)) + chunks


def build_apk(path: Path, *, certificate: bytes, package: str, version: tuple[str, int]) -> None:
    """Write an APK at ``path`` signed (in shape) by ``certificate``."""
    archive = io.BytesIO()
    with zipfile.ZipFile(archive, "w") as apk:
        apk.writestr("AndroidManifest.xml", manifest(package, *version))
    data = archive.getvalue()
    directory = struct.unpack_from("<I", data, data.rfind(b"PK\x05\x06") + 16)[0]
    signed_data = _prefixed() + _prefixed(_prefixed(certificate)) + _prefixed()
    signer = _prefixed(signed_data) + _prefixed() + _prefixed()
    value = _prefixed(_prefixed(signer))
    pair = struct.pack("<QI", 4 + len(value), _V2_ID) + value
    size = len(pair) + 8 + 16
    block = struct.pack("<Q", size) + pair + struct.pack("<Q", size) + b"APK Sig Block 42"
    head, tail = data[:directory], data[directory:]
    eocd = tail.rfind(b"PK\x05\x06")
    tail = tail[: eocd + 16] + struct.pack("<I", directory + len(block)) + tail[eocd + 20 :]
    path.write_bytes(head + block + tail)
