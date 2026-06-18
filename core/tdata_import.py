"""Adapter wrapping opentele2 — converts uploaded ``tdata.zip`` payloads.

Output: Telethon ``.session`` files in the configured sessions directory.

This is the ONLY place ``opentele2`` is imported, and it is imported lazily
inside conversion execution. Features and other ``core`` modules talk to this
module exclusively through the Pydantic schemas in ``schemas/tdata.py``.

Security guarantees:

- the zip is extracted into a private ``tempfile.mkdtemp`` directory; never into
  the project tree or the sessions directory directly.
- before extraction, every entry is validated: total uncompressed size, count,
  no absolute paths, no ``..`` components, no zip-encoded POSIX symlinks.
- the temp directory is wiped on every code path, including exceptions.
- only the resulting ``.session`` files survive in the configured sessions dir.
"""

from __future__ import annotations

import asyncio
import importlib
import io
import logging
import shutil
import tempfile
import zipfile
from contextlib import suppress
from pathlib import Path, PurePosixPath
from typing import Any

from core.logging import log_event
from schemas.tdata import (
    TdataAccountSummary,
    TdataConvertRequest,
    TdataConvertResult,
    TdataConvertStatus,
)

logger = logging.getLogger(__name__)

# Hard safety limits — anything above these is refused outright.
MAX_UNCOMPRESSED_BYTES = 500 * 1024 * 1024  # 500 MiB total uncompressed payload
MAX_FILE_COUNT = 50_000  # max entries in the zip
MAX_PATH_DEPTH = 32  # nested directory depth cap

_POSIX_CREATE_SYSTEM = 3
_POSIX_MODE_MASK = 0o170000
_POSIX_SYMLINK_MODE = 0o120000
TDesktop: Any | None = None
UseCurrentSession: Any = object()


def _opentele2_runtime() -> tuple[Any, Any]:
    global TDesktop, UseCurrentSession  # noqa: PLW0603 - cache lazy imports for conversion runs.
    if TDesktop is None:
        api_module = importlib.import_module("opentele2.api")
        td_module = importlib.import_module("opentele2.td")
        TDesktop = td_module.TDesktop
        UseCurrentSession = api_module.UseCurrentSession
    return TDesktop, UseCurrentSession


def _is_unsafe_entry(name: str) -> bool:
    """Return True if a zip entry name is unsafe (path traversal, absolute, ...)."""
    p = PurePosixPath(name.replace("\\", "/"))
    if p.is_absolute():
        return True
    parts = p.parts
    if any(part == ".." for part in parts):
        return True
    if any(":" in part for part in parts):  # windows-style drive fragments
        return True
    return len(parts) > MAX_PATH_DEPTH


def _is_symlink_entry(info: zipfile.ZipInfo) -> bool:
    """Return True if the zip entry is a POSIX symlink."""
    if info.create_system != _POSIX_CREATE_SYSTEM:
        return False
    mode = (info.external_attr >> 16) & _POSIX_MODE_MASK
    return mode == _POSIX_SYMLINK_MODE


def _safe_extract_zip(
    source: bytes | Path,
    dest: Path,
) -> TdataConvertStatus | None:
    """Validate the zip and extract into ``dest``.

    ``source`` may be the raw archive bytes (used by CLI / tests) or a Path to
    a temp file (UI uploads stream there to keep RAM flat). Returns None on
    success, or a non-ok ``TdataConvertStatus`` on rejection.
    """
    handle: io.BytesIO | Path = io.BytesIO(source) if isinstance(source, bytes) else source
    try:
        zf = zipfile.ZipFile(handle)
    except zipfile.BadZipFile:
        return "invalid_zip"

    with zf:
        infos = zf.infolist()
        if len(infos) > MAX_FILE_COUNT:
            return "too_many_files"
        total = 0
        for info in infos:
            if _is_unsafe_entry(info.filename):
                return "unsafe_path"
            if _is_symlink_entry(info):
                return "symlinks_not_allowed"
            total += info.file_size
            if total > MAX_UNCOMPRESSED_BYTES:
                return "zip_too_large"
        zf.extractall(dest)
    return None


def _find_tdata_dir(root: Path) -> Path | None:
    """Locate the ``tdata`` directory inside ``root``. Top-level first, then nested."""
    if (root / "tdata").is_dir():
        return root / "tdata"
    for sub in sorted(p for p in root.rglob("tdata") if p.is_dir()):
        return sub
    return None


async def convert_tdata_zip(
    req: TdataConvertRequest,
    sessions_dir: Path,
    *,
    tmp_base: Path | None = None,
) -> TdataConvertResult:
    """Convert a tdata.zip payload into Telethon ``.session`` files.

    Args:
        req: validated upload payload.
        sessions_dir: where to write the resulting ``.session`` files. Created if absent.
        tmp_base: optional directory under which the private temp dir is created.
            Useful for tests; defaults to the OS temp dir.

    Returns:
        ``TdataConvertResult`` with status and, on success, the list of accounts
        whose session files were written.

    A ``log_event`` is fired at every major step so a stuck import can be
    diagnosed from the activity feed — the last event before silence tells
    you where it hung.
    """
    sessions_dir.mkdir(parents=True, exist_ok=True)  # noqa: ASYNC240
    tmp_dir = Path(
        tempfile.mkdtemp(
            prefix="tdata_import_",
            dir=str(tmp_base) if tmp_base is not None else None,
        ),
    )
    await log_event(
        "INFO",
        "tdata_convert_started",
        extra={"filename": req.filename, "tmp_dir": str(tmp_dir)},
    )
    try:
        source: bytes | Path = req.content_path if req.content_path is not None else req.content
        # ZIP extraction of a multi-hundred-MB archive blocks for seconds —
        # do it on a worker thread so the event loop stays responsive (the UI
        # progress label must stay reactive while extraction runs).
        reject = await asyncio.to_thread(_safe_extract_zip, source, tmp_dir)
        if reject is not None:
            await log_event(
                "WARNING",
                "tdata_convert_zip_rejected",
                extra={"status": reject, "filename": req.filename},
            )
            return TdataConvertResult(status=reject)
        await log_event(
            "INFO",
            "tdata_convert_zip_extracted",
            extra={"filename": req.filename},
        )

        tdata_dir = await asyncio.to_thread(_find_tdata_dir, tmp_dir)
        if tdata_dir is None:
            await log_event(
                "WARNING",
                "tdata_convert_tdata_dir_not_found",
                extra={"filename": req.filename},
            )
            return TdataConvertResult(status="tdata_not_found")
        await log_event(
            "INFO",
            "tdata_convert_tdata_dir_found",
            extra={"tdata_dir": str(tdata_dir)},
        )

        tdesktop_factory, use_current_session = _opentele2_runtime()
        try:
            td = await asyncio.to_thread(tdesktop_factory, basePath=str(tdata_dir))
        except Exception as exc:
            logger.exception("TDesktop load failed")
            await log_event(
                "ERROR",
                "tdata_convert_tdesktop_load_failed",
                extra={"error_type": type(exc).__name__, "error": str(exc)},
            )
            return TdataConvertResult(
                status="conversion_error",
                error=f"TDesktop load failed: {exc}",
            )
        await log_event(
            "INFO",
            "tdata_convert_tdesktop_loaded",
            extra={"accounts_count": td.accountsCount},
        )

        if td.accountsCount == 0:
            return TdataConvertResult(status="no_accounts")

        summaries: list[TdataAccountSummary] = []
        for index, account in enumerate(td.accounts):
            user_id: int | None = None
            with suppress(Exception):
                user_id = account.UserId

            session_name = f"{user_id or f'tdata_{index}'}.session"
            session_path = sessions_dir / session_name

            await log_event(
                "INFO",
                "tdata_convert_account_starting",
                extra={
                    "index": index,
                    "user_id": user_id,
                    "session_path": str(session_path),
                },
            )

            try:
                client = await account.ToTelethon(
                    session=str(session_path),
                    flag=use_current_session,
                )
            except Exception as exc:
                logger.exception("ToTelethon failed for user_id=%s", user_id)
                await log_event(
                    "ERROR",
                    "tdata_convert_to_telethon_failed",
                    extra={
                        "index": index,
                        "user_id": user_id,
                        "error_type": type(exc).__name__,
                        "error": str(exc),
                    },
                )
                return TdataConvertResult(
                    status="conversion_error",
                    error=f"ToTelethon failed for user_id={user_id}: {exc}",
                    accounts=summaries,
                )

            with suppress(Exception):
                await client.disconnect()

            await log_event(
                "INFO",
                "tdata_convert_account_done",
                extra={"index": index, "user_id": user_id},
            )

            summaries.append(
                TdataAccountSummary(user_id=user_id, session_path=str(session_path)),
            )

        await log_event(
            "INFO",
            "tdata_convert_completed",
            extra={"accounts": len(summaries)},
        )
        return TdataConvertResult(status="ok", accounts=summaries)
    finally:
        shutil.rmtree(tmp_dir, ignore_errors=True)
