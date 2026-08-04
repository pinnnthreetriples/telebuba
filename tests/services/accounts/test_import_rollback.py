"""The row and the ``.session`` must never survive each other.

``add_account`` is not atomic: ``create_account`` COMMITS, and then
``get_or_create_device_fingerprint``, ``list_accounts`` and ``log_event`` follow —
all SQLite operations on a single-file datastore, so "database is locked", "disk
I/O error" and "database or disk is full" are ordinary outcomes there.

Every failure in this module is injected AFTER that commit, because a pre-commit
failure never produced the bad state: the first rollback shipped in this PR passed
its pre-commit test while deleting the sole credential of a live account on the
post-commit path.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

import pytest

from core.config import settings
from core.db import fetch_account
from core.device_fingerprint import get_or_create_device_fingerprint
from schemas.accounts import AccountCreate, AccountSessionFileImport
from schemas.tdata import TdataAccountSummary, TdataConvertRequest, TdataConvertResult
from schemas.telegram_session import TelegramSessionCheckResult
from services.accounts import add_account, import_account_session, import_account_tdata

if TYPE_CHECKING:
    from pathlib import Path

_CREDENTIAL = b"THE-ONLY-AUTH-KEY"
_FINGERPRINT_SEAM = "services.accounts.lifecycle.get_or_create_device_fingerprint"


def _break_after_commit(monkeypatch: pytest.MonkeyPatch) -> None:
    """Fail the first step that runs after ``create_account`` has committed."""

    async def _boom(_account_id: str) -> None:
        msg = "database is locked"
        raise RuntimeError(msg)

    monkeypatch.setattr(_FINGERPRINT_SEAM, _boom)


def _repair(monkeypatch: pytest.MonkeyPatch) -> None:
    """Put the real post-commit step back for a retry.

    Restores the one seam rather than calling ``monkeypatch.undo()``: the autouse
    ``_isolate_runtime`` fixture requests the same function-scoped ``monkeypatch``
    instance, so ``undo()`` would also revert ``session_dir`` and the temp database
    out from under the test.
    """
    monkeypatch.setattr(_FINGERPRINT_SEAM, get_or_create_device_fingerprint)


@pytest.mark.asyncio
async def test_a_post_commit_failure_removes_the_row_with_the_file(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The reported destruction: a live row whose only credential had been deleted.

    Before the row-aware rollback this left ``row_persisted=True`` and
    ``session_file_exists=False`` — an account the operator can see, cannot use, and
    cannot re-import over, because the surviving row is what refuses the retry.
    """
    _break_after_commit(monkeypatch)
    data = AccountSessionFileImport(filename="live.session", content=_CREDENTIAL)
    session_file = settings.telegram.session_dir / "live.session"

    with pytest.raises(RuntimeError, match="database is locked"):
        await import_account_session(data)

    assert await fetch_account("live") is None
    assert not session_file.exists()


@pytest.mark.asyncio
async def test_the_operator_can_retry_after_a_post_commit_failure(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Both halves gone means the retry has something to succeed at."""
    _break_after_commit(monkeypatch)
    data = AccountSessionFileImport(filename="live.session", content=_CREDENTIAL)
    with pytest.raises(RuntimeError, match="database is locked"):
        await import_account_session(data)

    _repair(monkeypatch)
    account = await import_account_session(data)
    assert account.account_id == "live"
    assert (settings.telegram.session_dir / "live.session").read_bytes() == _CREDENTIAL


@pytest.mark.asyncio
async def test_a_row_that_cannot_be_deleted_keeps_its_credential(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """If the row survives, the file MUST survive with it.

    This is why the rollback deletes the row first and bails out on failure. The
    other order — unlink, then delete — has exactly this branch destroying the
    credential of an account that is still in the database and still working.
    """
    _break_after_commit(monkeypatch)

    async def _undeletable(_account_id: str) -> None:
        msg = "database is locked"
        raise RuntimeError(msg)

    monkeypatch.setattr("services.accounts._import_rollback.delete_account", _undeletable)
    data = AccountSessionFileImport(filename="live.session", content=_CREDENTIAL)

    with pytest.raises(RuntimeError, match="database is locked"):
        await import_account_session(data)

    assert await fetch_account("live") is not None
    assert (settings.telegram.session_dir / "live.session").read_bytes() == _CREDENTIAL


@pytest.mark.asyncio
async def test_the_rollback_leaves_every_other_account_alone(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A rollback is scoped to the id being imported, row and file alike."""
    await add_account(AccountCreate(account_id="bystander", label="B", session_name="bystander"))
    bystander_file = settings.telegram.session_dir / "bystander.session"
    bystander_file.parent.mkdir(parents=True, exist_ok=True)
    bystander_file.write_bytes(b"someone-elses-credential")

    _break_after_commit(monkeypatch)
    with pytest.raises(RuntimeError, match="database is locked"):
        await import_account_session(
            AccountSessionFileImport(filename="live.session", content=_CREDENTIAL),
        )

    assert await fetch_account("bystander") is not None
    assert bystander_file.read_bytes() == b"someone-elses-credential"


@pytest.mark.asyncio
async def test_the_tdata_rollback_also_removes_a_post_commit_row(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """The same hole was already on main, one file over.

    ``_rollback_tdata_import`` gated the row delete on ``account_ids`` — the ids whose
    ``add_account`` RETURNED. The id whose ``add_account`` raised post-commit was
    therefore missing from it, so its committed row survived while its file was
    unlinked. The shared rollback decides by looking instead of by that list.
    """
    staging = tmp_path / "staging"
    staging.mkdir()
    (staging / "111.session").write_bytes(b"sess-111")

    async def fake_convert(_req: TdataConvertRequest, _dir: object) -> TdataConvertResult:
        return TdataConvertResult(
            status="ok",
            accounts=[TdataAccountSummary(user_id=111, session_path=str(staging / "111.session"))],
        )

    monkeypatch.setattr("services.accounts.sessions.convert_tdata_zip", fake_convert)
    _break_after_commit(monkeypatch)

    with pytest.raises(RuntimeError, match="database is locked"):
        await import_account_tdata(TdataConvertRequest(filename="tdata.zip", content=b"x"))

    assert await fetch_account("111") is None
    assert not (settings.telegram.session_dir / "111.session").exists()


@pytest.mark.asyncio
async def test_a_clean_tdata_batch_is_unaffected(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """Regression guard: the rollback rewrite must not touch the success path."""
    staging = tmp_path / "staging"
    staging.mkdir()
    (staging / "111.session").write_bytes(b"sess-111")

    async def fake_convert(_req: TdataConvertRequest, _dir: object) -> TdataConvertResult:
        return TdataConvertResult(
            status="ok",
            accounts=[TdataAccountSummary(user_id=111, session_path=str(staging / "111.session"))],
        )

    async def fake_check(request: object) -> TelegramSessionCheckResult:
        account_id = getattr(request, "account_id", "?")
        return TelegramSessionCheckResult(
            account_id=account_id,
            session_path=f"sessions/{account_id}",
            status="alive",
            is_temporary=False,
            user_id=111,
            username="u111",
        )

    monkeypatch.setattr("services.accounts.sessions.convert_tdata_zip", fake_convert)
    monkeypatch.setattr("services.accounts.sessions.check_telegram_session", fake_check)

    result = await import_account_tdata(TdataConvertRequest(filename="tdata.zip", content=b"x"))
    assert [a.account_id for a in result.accounts] == ["111"]
    assert await fetch_account("111") is not None
    assert (settings.telegram.session_dir / "111.session").exists()
