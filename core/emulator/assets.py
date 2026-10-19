"""The files a phone needs on the host, fetched once from their official source.

- ``scrcpy-server`` (the web screen): the configured version's asset of the
  github.com/Genymobile/scrcpy release, kept only when its sha256 is the configured one.

A file already in place is used as it is: nothing is ever updated behind the
operator's back. A download goes to a scratch file next to the target and is moved
into place only once its hash matched, so a cut download or a wrong file leaves
nothing behind. Concurrent callers wait for the one download in flight.
"""

from __future__ import annotations

import asyncio
import hashlib
import logging
from typing import TYPE_CHECKING

import httpx

from core.config import settings
from core.emulator._errors import EmulatorError

if TYPE_CHECKING:
    from pathlib import Path

    from schemas.emulator import EmulatorRefusalCode

_EMULATOR_CHECKSUM_MISMATCH: EmulatorRefusalCode = "emulator_checksum_mismatch"
_EMULATOR_DOWNLOAD_FAILED: EmulatorRefusalCode = "emulator_download_failed"

_SCRCPY_SERVER_URL = (
    "https://github.com/Genymobile/scrcpy/releases/download/v{version}/scrcpy-server-v{version}"
)
_CHUNK_BYTES = 1 << 16
# Larger than any phone file we fetch; a response past it is not the file we asked for.
_MAX_BYTES = 512 * 1024 * 1024

# Stdlib sink, step names only (see ``core.emulator._errors``).
logger = logging.getLogger(__name__)

_LOCKS: dict[str, asyncio.Lock] = {}


async def ensure_scrcpy_server() -> Path:
    """The scrcpy-server file, downloaded and hash-checked first when it is missing."""
    emulator = settings.emulator
    target = emulator.scrcpy_server_path
    url = _SCRCPY_SERVER_URL.format(version=emulator.scrcpy_server_version)
    await _ensure_file(target, url, emulator.scrcpy_server_sha256)
    return target


def _scratch_for(target: Path) -> Path | None:
    """Where a missing ``target`` is downloaded to; ``None`` when it is in place."""
    if target.is_file():
        return None
    target.parent.mkdir(parents=True, exist_ok=True)
    return target.with_name(f"{target.name}.part")


async def _ensure_file(target: Path, url: str, sha256: str) -> None:
    async with _LOCKS.setdefault(str(target), asyncio.Lock()):
        try:
            scratch = _scratch_for(target)
        except OSError as exc:  # the target's folder cannot be made
            raise EmulatorError(_EMULATOR_DOWNLOAD_FAILED) from exc
        if scratch is None:
            return
        try:
            digest = await _download(url, scratch)
            if digest != sha256:
                logger.warning("emulator asset %s failed its checksum", target.name)
                raise EmulatorError(_EMULATOR_CHECKSUM_MISMATCH)
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


async def _download(url: str, scratch: Path) -> str:
    """Stream ``url`` into ``scratch``; the sha256 of what was written."""
    digest = hashlib.sha256()
    written = 0
    try:
        async with (
            asyncio.timeout(settings.emulator.download_timeout_seconds),
            # httpx's own 5 s default would cut a CDN that pauses mid-file.
            httpx.AsyncClient(
                follow_redirects=True,
                timeout=settings.emulator.download_timeout_seconds,
                event_hooks={"request": [_refuse_plain_http]},
            ) as client,
            client.stream("GET", url) as response,
        ):
            response.raise_for_status()
            with scratch.open("wb") as out:
                async for chunk in response.aiter_bytes(_CHUNK_BYTES):
                    written += len(chunk)
                    if written > _MAX_BYTES:
                        raise EmulatorError(_EMULATOR_DOWNLOAD_FAILED)
                    digest.update(chunk)
                    out.write(chunk)
    except (httpx.HTTPError, TimeoutError, OSError) as exc:
        logger.warning("emulator asset download failed (%s)", type(exc).__name__)
        raise EmulatorError(_EMULATOR_DOWNLOAD_FAILED) from exc
    return digest.hexdigest()
