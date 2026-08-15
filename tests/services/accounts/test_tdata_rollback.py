"""tdata import rollback: what a mid-batch failure must undo and report.

Sibling of ``test_sessions.py`` (that file is at the 700-line test cap). Everything here
is about the rollback path — every placed session unlinked, every pooled handle evicted,
and an unlink that could not happen reported rather than swallowed.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

import pytest

from core.config import settings
from schemas.tdata import TdataAccountSummary, TdataConvertRequest, TdataConvertResult
from schemas.telegram_session import TelegramSessionCheckResult
from services.accounts import import_account_tdata, list_accounts

if TYPE_CHECKING:
    from pathlib import Path


@pytest.mark.asyncio
async def test_import_account_tdata_rolls_back_on_mid_batch_failure(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """Mid-batch failure must leave the DB and disk in their pre-import state."""
    staging = tmp_path / "staging"
    staging.mkdir()
    (staging / "111.session").write_bytes(b"sess-111")
    (staging / "222.session").write_bytes(b"sess-222")

    async def fake_convert(_req: TdataConvertRequest, _dir: object) -> TdataConvertResult:
        return TdataConvertResult(
            status="ok",
            accounts=[
                TdataAccountSummary(user_id=111, session_path=str(staging / "111.session")),
                TdataAccountSummary(user_id=222, session_path=str(staging / "222.session")),
            ],
        )

    call_count = {"n": 0}

    async def flaky_check(request: object) -> TelegramSessionCheckResult:
        call_count["n"] += 1
        if call_count["n"] == 2:
            msg = "boom on second account"
            raise RuntimeError(msg)
        account_id = getattr(request, "account_id", "?")
        return TelegramSessionCheckResult(
            account_id=account_id,
            session_path=f"sessions/{account_id}",
            status="alive",
            is_temporary=False,
            user_id=int(account_id),
            username=f"u{account_id}",
        )

    monkeypatch.setattr("services.accounts.sessions.convert_tdata_zip", fake_convert)
    monkeypatch.setattr("services.accounts.sessions.check_telegram_session", flaky_check)

    with pytest.raises(RuntimeError, match=r"boom"):
        await import_account_tdata(
            TdataConvertRequest(filename="tdata.zip", content=b"x"),
        )

    persisted = await list_accounts()
    assert persisted.accounts == []
    final_dir = settings.telegram.session_dir
    assert not (final_dir / "111.session").exists()
    assert not (final_dir / "222.session").exists()


def _stage_failing_import(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """Two staged tdata accounts where the second account's check explodes.

    Same shape as ``test_import_account_tdata_rolls_back_on_mid_batch_failure``,
    reused by the two rollback-mechanics tests below.
    """
    staging = tmp_path / "staging"
    staging.mkdir()
    (staging / "111.session").write_bytes(b"sess-111")
    (staging / "222.session").write_bytes(b"sess-222")

    async def fake_convert(_req: TdataConvertRequest, _dir: object) -> TdataConvertResult:
        return TdataConvertResult(
            status="ok",
            accounts=[
                TdataAccountSummary(user_id=111, session_path=str(staging / "111.session")),
                TdataAccountSummary(user_id=222, session_path=str(staging / "222.session")),
            ],
        )

    calls = {"n": 0}

    async def flaky_check(request: object) -> TelegramSessionCheckResult:
        calls["n"] += 1
        if calls["n"] == 2:
            msg = "boom on second account"
            raise RuntimeError(msg)
        account_id = getattr(request, "account_id", "?")
        return TelegramSessionCheckResult(
            account_id=account_id,
            session_path=f"sessions/{account_id}",
            status="alive",
            is_temporary=False,
        )

    monkeypatch.setattr("services.accounts.sessions.convert_tdata_zip", fake_convert)
    monkeypatch.setattr("services.accounts.sessions.check_telegram_session", flaky_check)


@pytest.mark.asyncio
async def test_tdata_rollback_evicts_the_pooled_client_per_placed_account(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """The rollback runs under the pool tombstone, like ``lifecycle.remove_account``.

    ``check_account_session`` has already pooled a client for every placed
    account, and on Windows that live ``.session`` handle is what makes the
    rollback's unlink raise — leaving an orphan file with no DB row, which
    preflight then refuses to re-import forever.
    """
    _stage_failing_import(monkeypatch, tmp_path)

    evicted: list[str] = []

    async def fake_evict(account_id: str) -> None:
        evicted.append(account_id)

    monkeypatch.setattr("core.telegram_client._pool.evict_client", fake_evict)

    with pytest.raises(RuntimeError, match=r"boom"):
        await import_account_tdata(TdataConvertRequest(filename="tdata.zip", content=b"x"))

    assert evicted == ["111", "222"], "every placed session must be unlinked with no live handle"


@pytest.mark.asyncio
async def test_tdata_rollback_logs_an_unlink_it_could_not_perform(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """A failed unlink is reported, never swallowed.

    The silent ``suppress(OSError)`` is what turned a retryable import failure
    into a permanent, operator-unfixable block: the orphaned ``.session`` has no
    account row to delete through the UI, and preflight refuses the retry with
    "delete it before importing". The file may survive — the log entry is what
    tells the operator which one to remove.
    """
    _stage_failing_import(monkeypatch, tmp_path)

    events: list[str] = []
    payloads: dict[str, dict[str, object]] = {}

    async def fake_log(
        level: str,  # noqa: ARG001
        event: str,
        account_id: str | None = None,  # noqa: ARG001
        extra: dict[str, object] | None = None,
    ) -> None:
        events.append(event)
        payloads[event] = extra or {}

    def refuse_unlink(_self: object, *, missing_ok: bool = False) -> None:  # noqa: ARG001
        # What Windows does when the pooled client still holds the handle.
        msg = "used by another process"
        raise OSError(msg)

    monkeypatch.setattr("services.accounts._tdata.log_event", fake_log)
    monkeypatch.setattr("pathlib.Path.unlink", refuse_unlink)

    with pytest.raises(RuntimeError, match=r"boom"):
        await import_account_tdata(TdataConvertRequest(filename="tdata.zip", content=b"x"))

    assert "tdata_rollback_unlink_failed" in events
    assert "tdata_import_rolled_back" in events
    assert payloads["tdata_import_rolled_back"]["files"] == ["111.session", "222.session"]
    assert str(settings.telegram.session_dir) not in str(payloads)
