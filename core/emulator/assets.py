"""The files a phone needs on the host, fetched once from their official source.

- ``scrcpy-server`` (the web screen): the configured version's asset of the
  github.com/Genymobile/scrcpy release, kept only when its sha256 is the configured one;
- microG's APKs (``core.emulator._microg``): the pinned release files from microG's
  GitHub releases, each kept only when its sha256 is the pinned one;
- the Telegram APK: telegram.org's own download (always its latest build, package
  ``org.telegram.messenger.web``, no published checksum), kept only when it is signed
  with Telegram's certificate (``TELEGRAM_CERTIFICATE_SHA256``). Android checks the
  signature against that certificate on install, so the two together mean the app
  is Telegram's. Its package and version are read from the file itself;
- the Госуслуги APK (``core.emulator._gosuslugi``): RuStore's current build, fetched
  through the phone's own proxy and kept only when signed with its pinned certificate.

A file already in place is used as it is (an APK the operator put there included):
nothing is ever updated behind the operator's back. A download goes to a scratch
file next to the target and is moved into place only once it passed its check, so a
cut download or a wrong file leaves nothing behind. Concurrent callers wait for the
one download in flight.
"""

from __future__ import annotations

import asyncio
import hashlib
import logging
from typing import TYPE_CHECKING
from urllib.parse import quote

import httpx

from core.android_apk import AppIdentity, app_identity, signing_certificate_sha256
from core.config import settings
from core.emulator import _microg
from core.emulator._errors import EmulatorError

if TYPE_CHECKING:
    from collections.abc import Awaitable, Callable
    from pathlib import Path
    from typing import BinaryIO

    from schemas.emulator import EmulatorRefusalCode
    from schemas.proxy import ProxySettings

# Where a file comes from: its URL, or a call that looks the URL up (a store's API).
type FileSource = str | Callable[[], Awaitable[str]]

_EMULATOR_CHECKSUM_MISMATCH: EmulatorRefusalCode = "emulator_checksum_mismatch"
_EMULATOR_DOWNLOAD_FAILED: EmulatorRefusalCode = "emulator_download_failed"
_EMULATOR_INSTALL_FAILED: EmulatorRefusalCode = "emulator_install_failed"
_EMULATOR_SIGNATURE_MISMATCH: EmulatorRefusalCode = "emulator_signature_mismatch"

_SCRCPY_SERVER_URL = (
    "https://github.com/Genymobile/scrcpy/releases/download/v{version}/scrcpy-server-v{version}"
)
_TELEGRAM_APK_URL = "https://telegram.org/dl/android/apk"
# Telegram for Android's signing certificate (CN=Nikolay Kudashov, O=VK, 2013-2038):
# the same on every build telegram.org has served, ``apksigner --print-certs``'s SHA-256.
TELEGRAM_CERTIFICATE_SHA256 = "49c1522548ebacd46ce322b6fd47f6092bb745d0f88082145caf35e14dcc38e1"
_CHUNK_BYTES = 1 << 16
# Larger than any phone file we fetch; a response past it is not the file we asked for.
_MAX_BYTES = 512 * 1024 * 1024
# A connection silent this long is stalled: it is dropped and the download resumed
# where it stopped (a ``Range`` request), at most ``_RESUMES`` times.
_STALL_SECONDS = 60.0
_RESUMES = 5

# Stdlib sink, step names only (see ``core.emulator._errors``).
logger = logging.getLogger(__name__)

_LOCKS: dict[str, asyncio.Lock] = {}


async def ensure_scrcpy_server() -> Path:
    """The scrcpy-server file, downloaded and hash-checked first when it is missing."""
    emulator = settings.emulator
    target = emulator.scrcpy_server_path
    url = _SCRCPY_SERVER_URL.format(version=emulator.scrcpy_server_version)
    await ensure_file(target, url, _hashed(emulator.scrcpy_server_sha256))
    return target


async def ensure_microg() -> dict[str, Path]:
    """The microG APKs by ``_microg.APPS`` name, the missing downloaded first; none when off."""
    directory = settings.emulator.microg_dir
    if directory is None:
        return {}
    apks: dict[str, Path] = {}
    for app in _microg.APPS:
        target = directory / app.file_name
        await ensure_file(target, app.url, _hashed(app.sha256))
        apks[app.name] = target
    return apks


def _hashed(sha256: str) -> Callable[[Path, str], Awaitable[None]]:
    """A check that refuses a download whose sha256 is not ``sha256``."""

    async def same_hash(_scratch: Path, digest: str) -> None:
        if digest != sha256:
            raise EmulatorError(_EMULATOR_CHECKSUM_MISMATCH)

    return same_hash


async def ensure_telegram_apk() -> AppIdentity:
    """The Telegram APK's package and version, downloaded and checked first when missing.

    A file that is no APK (or one with no package or version in it) is
    ``emulator_install_failed``: nothing could be installed from it.
    """
    target = settings.emulator.telegram_apk_path
    await ensure_file(target, _TELEGRAM_APK_URL, signed_by(TELEGRAM_CERTIFICATE_SHA256))
    try:
        return await asyncio.to_thread(app_identity, target)
    except (ValueError, OSError) as exc:
        raise EmulatorError(_EMULATOR_INSTALL_FAILED) from exc


def signed_by(certificate_sha256: str) -> Callable[[Path, str], Awaitable[None]]:
    """A check that refuses an APK not signed with exactly that certificate."""

    async def same_signer(scratch: Path, _digest: str) -> None:
        try:
            certificate = await asyncio.to_thread(signing_certificate_sha256, scratch)
        except ValueError as exc:
            raise EmulatorError(_EMULATOR_SIGNATURE_MISMATCH) from exc
        if certificate != certificate_sha256:
            raise EmulatorError(_EMULATOR_SIGNATURE_MISMATCH)

    return same_signer


def _scratch_for(target: Path) -> Path | None:
    """Where a missing ``target`` is downloaded to; ``None`` when it is in place."""
    if target.is_file():
        return None
    target.parent.mkdir(parents=True, exist_ok=True)
    return target.with_name(f"{target.name}.part")


async def ensure_file(
    target: Path,
    source: FileSource,
    check: Callable[[Path, str], Awaitable[None]],
    *,
    proxy: ProxySettings | None = None,
    seconds: float | None = None,
) -> None:
    """Download a missing ``target``; ``check`` (scratch file, its sha256) refuses a wrong one.

    ``source`` is looked up only when the file is missing; ``proxy`` carries both the
    lookup's and the download's traffic, ``seconds`` bounds the download (default:
    ``download_timeout_seconds``).
    """
    async with _LOCKS.setdefault(str(target), asyncio.Lock()):
        try:
            scratch = _scratch_for(target)
        except OSError as exc:  # the target's folder cannot be made
            raise EmulatorError(_EMULATOR_DOWNLOAD_FAILED) from exc
        if scratch is None:
            return
        try:
            url = source if isinstance(source, str) else await source()
            digest = await _download(url, scratch, proxy=proxy, seconds=seconds)
            try:
                await check(scratch, digest)
            except EmulatorError as exc:
                logger.warning("emulator asset %s refused (%s)", target.name, exc.code)
                raise
            try:
                scratch.replace(target)
            except OSError as exc:
                raise EmulatorError(_EMULATOR_DOWNLOAD_FAILED) from exc
        finally:
            scratch.unlink(missing_ok=True)
    logger.info("emulator asset %s downloaded", target.name)


async def _refuse_plain_http(request: httpx.Request) -> None:
    """Run before every hop, redirects too: nothing is ever asked for off TLS."""
    if request.url.scheme != "https":
        raise EmulatorError(_EMULATOR_DOWNLOAD_FAILED)


def http_client(proxy: ProxySettings | None, seconds: float) -> httpx.AsyncClient:
    """A TLS-only client, through ``proxy`` when given (its credentials stay in the URL)."""
    return httpx.AsyncClient(
        follow_redirects=True,
        # httpx's own 5 s default would cut a CDN that pauses mid-file; a longer
        # silence is a stall, resumed by ``_download``.
        timeout=min(seconds, _STALL_SECONDS),
        event_hooks={"request": [_refuse_plain_http]},
        proxy=None if proxy is None else _proxy_url(proxy),
    )


def _proxy_url(proxy: ProxySettings) -> str:
    scheme = "socks5" if proxy.proxy_type == "socks5" else "http"
    auth = ""
    if proxy.username:
        auth = f"{quote(proxy.username, safe='')}:{quote(proxy.password or '', safe='')}@"
    return f"{scheme}://{auth}{proxy.host}:{proxy.port}"


async def _download(
    url: str, scratch: Path, *, proxy: ProxySettings | None = None, seconds: float | None = None
) -> str:
    """Stream ``url`` into ``scratch``; the sha256 of what was written.

    A dropped or stalled connection is resumed from the bytes already written; a
    server that will not resume (no 206) fails the download.
    """
    digest = hashlib.sha256()
    seconds = seconds or settings.emulator.download_timeout_seconds
    try:
        async with asyncio.timeout(seconds), http_client(proxy, seconds) as client:
            with scratch.open("wb") as out:
                for resumes_left in range(_RESUMES, -1, -1):
                    try:
                        await _stream_into(client, url, out, digest)
                        break
                    except httpx.TransportError:
                        if not resumes_left:
                            raise
                        logger.info("emulator asset download resumed at %d bytes", out.tell())
    except (httpx.HTTPError, TimeoutError, OSError) as exc:
        logger.warning("emulator asset download failed (%s)", type(exc).__name__)
        raise EmulatorError(_EMULATOR_DOWNLOAD_FAILED) from exc
    return digest.hexdigest()


async def _stream_into(
    client: httpx.AsyncClient, url: str, out: BinaryIO, digest: hashlib._Hash
) -> None:
    """Append ``url``'s bytes from ``out``'s position on, ``digest`` taking them in."""
    written = out.tell()
    headers = {"Range": f"bytes={written}-"} if written else None
    async with client.stream("GET", url, headers=headers) as response:
        response.raise_for_status()
        if written and response.status_code != httpx.codes.PARTIAL_CONTENT:
            raise EmulatorError(_EMULATOR_DOWNLOAD_FAILED)
        async for chunk in response.aiter_bytes(_CHUNK_BYTES):
            written += len(chunk)
            if written > _MAX_BYTES:
                raise EmulatorError(_EMULATOR_DOWNLOAD_FAILED)
            digest.update(chunk)
            out.write(chunk)
