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

import asyncio
import pathlib
from typing import TYPE_CHECKING

import pytest

from core.config import settings
from core.db import fetch_account
from core.device_fingerprint import get_or_create_device_fingerprint
from schemas.accounts import AccountCreate, AccountSessionFileImport
from schemas.tdata import TdataAccountSummary, TdataConvertRequest, TdataConvertResult
from schemas.telegram_session import TelegramSessionCheckResult
from services.accounts import (
    SessionAlreadyExistsError,
    add_account,
    import_account_session,
    import_account_tdata,
    start_phone_login,
)

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
async def test_a_concurrent_phone_login_row_is_not_deleted_by_a_rollback(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The rollback must not delete a row some OTHER writer created.

    ``start_phone_login`` also inserts account rows, and it did not take
    ``import_lock`` — so a phone whose digits equal an uploaded session's stem could
    land inside a failed import's window, and the import's rollback then deleted the
    operator's account. The lock is what makes "a row here is one this import
    created" true, so the login path takes it too.

    The import here fails BEFORE committing a row of its own, so the only row that
    can exist when the rollback looks is the login's. Under the lock the login cannot
    get past `import_lock` while the import holds it, so the rollback sees nothing and
    the login's row lands afterwards, intact. Without the lock the login completes
    inside the window and the rollback deletes it.
    """
    session_name = "79001234567"
    logins: list[asyncio.Task[object]] = []

    async def _let_a_login_in_then_fail(_create: object) -> None:
        # A pre-commit refusal: this import never gets a row of its own.
        logins.append(asyncio.create_task(start_phone_login(f"+{session_name}")))
        # Long enough for an UNLOCKED login to finish its insert. A locked one cannot
        # progress past the lock no matter how long this waits, so the passing
        # direction does not depend on the duration.
        await asyncio.sleep(0.25)
        msg = "database is locked"
        raise RuntimeError(msg)

    monkeypatch.setattr("services.accounts.sessions.add_account", _let_a_login_in_then_fail)
    with pytest.raises(RuntimeError, match="database is locked"):
        await import_account_session(
            AccountSessionFileImport(
                filename=f"{session_name}.session",
                content=_CREDENTIAL,
            ),
        )

    await asyncio.gather(*logins)
    assert await fetch_account(session_name) is not None


@pytest.mark.asyncio
async def test_a_leftover_file_says_so_instead_of_naming_a_missing_account(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """``file_kept`` leaves no account, so the refusal must blame the FILE.

    The single message told the operator to "delete the account first" when the
    rollback had already removed the row — naming something absent from the UI, with
    no remedy. The residual is the file, so the message names the file.
    """
    _break_after_commit(monkeypatch)
    original_unlink = pathlib.Path.unlink

    def _unlink(_self: Path, *, missing_ok: bool = False) -> None:  # noqa: ARG001 - mirrors Path.unlink
        raise PermissionError(32, "file in use")

    monkeypatch.setattr("pathlib.Path.unlink", _unlink)
    data = AccountSessionFileImport(filename="live.session", content=_CREDENTIAL)
    with pytest.raises(RuntimeError, match="database is locked"):
        await import_account_session(data)

    # Row gone, file stranded — the state the old message described wrongly.
    assert await fetch_account("live") is None
    assert (settings.telegram.session_dir / "live.session").exists()

    # Restore ONLY the unlink seam. ``monkeypatch.undo()`` would also revert the
    # autouse ``_isolate_runtime`` fixture's ``session_dir`` redirect — it requests
    # this same function-scoped instance — pointing the second import at the REAL
    # sessions directory, where it passes both pre-checks and writes a credential
    # into the working tree. Same reason ``_repair`` exists; it happened.
    monkeypatch.setattr("pathlib.Path.unlink", original_unlink)
    assert settings.telegram.session_dir.is_relative_to(tmp_path), "sandbox escaped"

    with pytest.raises(SessionAlreadyExistsError) as caught:
        await import_account_session(data)
    message = str(caught.value)
    assert "live.session" in message
    assert "Remove that file" in message
    assert "already exists" not in message


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
async def test_an_already_absent_file_is_not_reported_as_a_failed_rollback(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """``missing_ok=True``, and this is what it buys.

    The rollback's job is to reach "neither the row nor the file exists". If the
    file is already gone when it runs, that job is done — but a bare ``unlink``
    raises ``FileNotFoundError``, which is an ``OSError``, so the outcome would come
    back ``file_kept`` and the operator would be told
    ``account_session_import_rollback_failed`` about a file that is not there. The
    precedent this rollback follows, ``_tdata._rollback_tdata_import``, already
    passed ``missing_ok=True`` for exactly this reason.
    """
    events: list[str] = []

    async def fake_log(
        _level: str,
        event: str,
        account_id: str | None = None,  # noqa: ARG001
        extra: dict[str, object] | None = None,  # noqa: ARG001
    ) -> None:
        events.append(event)

    session_file = settings.telegram.session_dir / "live.session"

    async def _boom_after_losing_the_file(_account_id: str) -> None:
        session_file.unlink()  # something else got there first
        msg = "database is locked"
        raise RuntimeError(msg)

    monkeypatch.setattr(_FINGERPRINT_SEAM, _boom_after_losing_the_file)
    monkeypatch.setattr("services.accounts.sessions.log_event", fake_log)

    with pytest.raises(RuntimeError, match="database is locked"):
        await import_account_session(
            AccountSessionFileImport(filename="live.session", content=_CREDENTIAL),
        )

    assert "account_session_import_rollback_failed" not in events
    assert "account_session_import_rolled_back" in events
    assert await fetch_account("live") is None


@pytest.mark.asyncio
async def test_a_failed_tdata_unlink_still_reports_the_error_class(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """``error_type`` distinguishes a live handle from a full disk.

    ``origin/main`` logged it and the first rework dropped it, so the operator could
    no longer tell ``PermissionError`` from ``OSError`` on ``ENOSPC`` — two different
    remedies behind one message. A bounded class name, never ``str(exc)``: this rides
    an ``extra`` payload that ``GET /logs`` serves back, and a ``PermissionError``'s
    text carries the absolute session path.
    """
    events: list[dict[str, object]] = []

    async def _capture(
        _level: str,
        event: str,
        account_id: str | None = None,  # noqa: ARG001 - mirrors log_event
        extra: dict[str, object] | None = None,
    ) -> None:
        events.append({"event": event, **(extra or {})})

    staging = tmp_path / "staging"
    staging.mkdir()
    (staging / "111.session").write_bytes(b"sess-111")

    async def fake_convert(_req: TdataConvertRequest, _dir: object) -> TdataConvertResult:
        return TdataConvertResult(
            status="ok",
            accounts=[TdataAccountSummary(user_id=111, session_path=str(staging / "111.session"))],
        )

    def _unlink(_self: Path, *, missing_ok: bool = False) -> None:  # noqa: ARG001 - mirrors Path.unlink
        raise PermissionError(32, "file in use")

    monkeypatch.setattr("services.accounts.sessions.convert_tdata_zip", fake_convert)
    monkeypatch.setattr("services.accounts._tdata.log_event", _capture)
    _break_after_commit(monkeypatch)
    monkeypatch.setattr("pathlib.Path.unlink", _unlink)

    with pytest.raises(RuntimeError, match="database is locked"):
        await import_account_tdata(TdataConvertRequest(filename="tdata.zip", content=b"x"))

    failures = [e for e in events if e["event"] == "tdata_rollback_unlink_failed"]
    assert failures, events
    assert failures[0]["error_type"] == "PermissionError"
    # The residual rides ``reason``, which the SPA's reason column renders beside
    # ``error_type`` — a ``kept`` key was persisted and shown nowhere.
    assert failures[0]["reason"] == "file_kept"


@pytest.mark.asyncio
async def test_the_rollback_failure_event_carries_only_rendered_fields(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """One meaning per key, and nothing persisted that the UI cannot show.

    ``error_type`` is the class of the failure the event is NAMED after — the
    rollback's own — the same meaning ``tdata_rollback_unlink_failed`` carries, so two
    adjacent rows no longer put two different things under the reason column. The
    residual moved to ``reason``, which ``shared/lib/log/eventReason.ts`` renders
    beside it. ``kept`` and ``cause_type`` are gone: both were written to the ``logs``
    table, served by ``GET /logs``, and displayed nowhere.
    """
    payloads: list[dict[str, object]] = []

    async def fake_log(
        _level: str,
        event: str,
        account_id: str | None = None,  # noqa: ARG001
        extra: dict[str, object] | None = None,
    ) -> None:
        if event == "account_session_import_rollback_failed":
            payloads.append(extra or {})

    def _unlink(_self: Path, *, missing_ok: bool = False) -> None:  # noqa: ARG001 - mirrors Path.unlink
        raise PermissionError(32, "file in use")

    _break_after_commit(monkeypatch)
    monkeypatch.setattr("services.accounts.sessions.log_event", fake_log)
    monkeypatch.setattr("pathlib.Path.unlink", _unlink)

    with pytest.raises(RuntimeError, match="database is locked"):
        await import_account_session(
            AccountSessionFileImport(filename="live.session", content=_CREDENTIAL),
        )

    assert payloads, "the failed rollback reported nothing"
    assert payloads[0] == {
        "session_name": "live",
        "reason": "file_kept",
        "error_type": "PermissionError",
    }


@pytest.mark.asyncio
async def test_a_kept_row_also_reports_through_the_same_event(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """``tdata_rollback_unlink_failed`` fires for a kept ROW too, not just a kept file.

    That is why its label could no longer say "could not remove the session file":
    on this branch the file was deliberately kept and the row delete is what failed.
    The trigger is deliberately wide — both residuals need reporting — so the wording
    is what had to change.
    """
    payloads: list[dict[str, object]] = []

    async def fake_log(
        _level: str,
        event: str,
        account_id: str | None = None,  # noqa: ARG001
        extra: dict[str, object] | None = None,
    ) -> None:
        if event == "tdata_rollback_unlink_failed":
            payloads.append(extra or {})

    async def _undeletable(_account_id: str) -> None:
        msg = "database is locked"
        raise RuntimeError(msg)

    staging = tmp_path / "staging"
    staging.mkdir()
    (staging / "111.session").write_bytes(b"sess-111")

    async def fake_convert(_req: TdataConvertRequest, _dir: object) -> TdataConvertResult:
        return TdataConvertResult(
            status="ok",
            accounts=[TdataAccountSummary(user_id=111, session_path=str(staging / "111.session"))],
        )

    monkeypatch.setattr("services.accounts.sessions.convert_tdata_zip", fake_convert)
    monkeypatch.setattr("services.accounts._tdata.log_event", fake_log)
    _break_after_commit(monkeypatch)
    monkeypatch.setattr("services.accounts._import_rollback.delete_account", _undeletable)

    with pytest.raises(RuntimeError, match="database is locked"):
        await import_account_tdata(TdataConvertRequest(filename="tdata.zip", content=b"x"))

    assert payloads, "a kept row reported nothing"
    assert payloads[0]["reason"] == "row_kept"
    assert payloads[0]["error_type"] == "RuntimeError"
    # The row survived, so its credential had to survive with it.
    assert await fetch_account("111") is not None
    assert (settings.telegram.session_dir / "111.session").exists()


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
