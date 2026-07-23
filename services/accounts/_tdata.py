"""Tdata-archive import: convert → preflight → place → rollback.

Splitting this out of the package ``__init__`` keeps the orchestration helpers
(plan, preflight, rollback) close to ``import_account_tdata`` while preventing
the public service module from growing past the size gate.
"""

from __future__ import annotations

import shutil
import tempfile
from contextlib import AsyncExitStack, suppress
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Any

from core.config import settings
from core.db import delete_account, fetch_account
from core.logging import log_event
from schemas.accounts import AccountCheckRequest, AccountCreate
from services.accounts._import_locks import import_lock

if TYPE_CHECKING:
    from schemas.accounts import AccountRead
    from schemas.tdata import TdataConvertRequest


@dataclass(frozen=True)
class _TdataAccountPlan:
    account_id: str
    session_name: str
    staging_path: Path
    final_path: Path


class SessionAlreadyExistsError(ValueError):
    """Raised when an import would overwrite an existing account's session.

    A ``.session`` file is effectively a Telegram credential — re-uploading a
    file with the same name must not silently replace what is already there.
    The operator has to delete the existing account first if they really want
    to swap credentials.
    """


async def _preflight_tdata_plans(plans: list[_TdataAccountPlan]) -> None:
    """Refuse to start the import if any account_id / file would clobber existing state."""
    for plan in plans:
        if await fetch_account(plan.account_id) is not None:
            msg = f"tdata account {plan.account_id!r} already exists. Delete it before importing."
            raise SessionAlreadyExistsError(msg)
        if plan.final_path.exists() and plan.staging_path.resolve() != plan.final_path.resolve():
            msg = (
                f"session file {plan.final_path.name!r} already exists. Delete it before importing."
            )
            raise SessionAlreadyExistsError(msg)


async def _rollback_tdata_import(account_ids: list[str], session_files: list[Path]) -> None:
    """Best-effort: remove DB rows + .session files written during a failed import."""
    for account_id in account_ids:
        with suppress(Exception):
            await delete_account(account_id)
    for session_file in session_files:
        with suppress(OSError):
            session_file.unlink()
    await log_event(
        "WARNING",
        "tdata_import_rolled_back",
        extra={"accounts": account_ids, "files": [str(p) for p in session_files]},
    )


async def import_account_tdata(
    data: TdataConvertRequest,
    *,
    convert: Any,  # noqa: ANN401 - DI seam; richer protocol would just duplicate convert_tdata_zip's signature.
    add_account: Any,  # noqa: ANN401
    check_account_session: Any,  # noqa: ANN401
) -> list[AccountRead]:
    """Atomic conversion of a tdata.zip into ``.session`` files + DB rows.

    Convert runs first (staging-only, no side effects on the final dir). Then
    preflight checks that every produced account_id is free in both the DB and
    the final sessions dir; if any conflict, the import aborts before touching
    the final dir. Finally, files are moved and accounts added one-by-one — on
    any mid-batch failure, every change made so far is rolled back so the
    caller never observes a partial import.

    ``convert``, ``add_account``, and ``check_account_session`` are injected
    by the package ``__init__`` so tests can monkeypatch them at the public
    service boundary.

    Conversion targets a private staging dir (never the live sessions dir), so a
    re-import that collides with an existing credential can't overwrite it before
    preflight runs. The staging dir sits beside the sessions dir (same volume →
    the post-preflight move is a rename) and is wiped on both success and failure.
    """
    session_dir = settings.telegram.session_dir
    session_dir.mkdir(parents=True, exist_ok=True)
    staging_dir = Path(tempfile.mkdtemp(prefix="tdata_staging_", dir=str(session_dir.parent)))
    try:
        return await _run_tdata_import(
            data,
            staging_dir,
            convert=convert,
            add_account=add_account,
            check_account_session=check_account_session,
        )
    finally:
        shutil.rmtree(staging_dir, ignore_errors=True)


async def _run_tdata_import(
    data: TdataConvertRequest,
    staging_dir: Path,
    *,
    convert: Any,  # noqa: ANN401 - DI seam mirrors import_account_tdata.
    add_account: Any,  # noqa: ANN401
    check_account_session: Any,  # noqa: ANN401
) -> list[AccountRead]:
    result = await convert(data, staging_dir)
    if result.status != "ok":
        msg = f"tdata import failed: {result.status}"
        if result.error:
            msg = f"{msg} — {result.error}"
        await log_event(
            "ERROR",
            "tdata_import_failed",
            extra={"status": result.status, "error": result.error},
        )
        raise ValueError(msg)
    if not result.accounts:
        msg = "tdata contained no accounts"
        await log_event("WARNING", "tdata_no_accounts", extra={"filename": data.filename})
        raise ValueError(msg)

    plans: list[_TdataAccountPlan] = []
    for summary in result.accounts:
        staging_path = Path(summary.session_path)
        session_name = staging_path.stem
        account_id = str(summary.user_id) if summary.user_id is not None else session_name
        final_path = settings.telegram.session_dir / staging_path.name
        plans.append(_TdataAccountPlan(account_id, session_name, staging_path, final_path))

    # Hold one import lock per produced account_id across preflight→place→add and
    # the rollback, so a concurrent same-key import (or its rollback) can't race
    # this one — it will see the account exist at preflight and abort instead of
    # deleting a row/file this import just wrote. Keys are locked in sorted order
    # so two overlapping tdata imports can never deadlock on opposing orders.
    keys = sorted({plan.account_id for plan in plans})
    placed_files: list[Path] = []
    added_account_ids: list[str] = []
    checked: list[AccountRead] = []
    async with AsyncExitStack() as locks:
        for key in keys:
            await locks.enter_async_context(import_lock(key))

        await _preflight_tdata_plans(plans)

        try:
            for plan in plans:
                if plan.staging_path.resolve() != plan.final_path.resolve():
                    plan.final_path.parent.mkdir(parents=True, exist_ok=True)
                    shutil.move(str(plan.staging_path), str(plan.final_path))
                placed_files.append(plan.final_path)
                await add_account(
                    AccountCreate(
                        account_id=plan.account_id,
                        label=data.label or plan.account_id,
                        session_name=plan.session_name,
                    ),
                )
                added_account_ids.append(plan.account_id)
                checked.append(
                    await check_account_session(
                        AccountCheckRequest(account_id=plan.account_id),
                    ),
                )
        except Exception:
            await _rollback_tdata_import(added_account_ids, placed_files)
            raise

    await log_event(
        "INFO",
        "tdata_import_completed",
        extra={"imported": len(checked)},
    )
    return checked
