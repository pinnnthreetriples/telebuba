"""Behavioral rollback and audit contracts exposed by the mutation sweep."""

from __future__ import annotations

from typing import TYPE_CHECKING

import pytest

from core.config import settings
from schemas.tdata import TdataAccountSummary, TdataConvertRequest, TdataConvertResult
from schemas.telegram_session import TelegramSessionCheckResult
from services.accounts import import_account_tdata

if TYPE_CHECKING:
    from pathlib import Path

    from schemas.accounts import AccountCheckRequest


def _configure_failing_two_account_import(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    *,
    first_id: str,
    second_id: str,
) -> None:
    staging = tmp_path / f"staging-{first_id}"
    staging.mkdir()
    summaries: list[TdataAccountSummary] = []
    for account_id in (first_id, second_id):
        session_path = staging / f"{account_id}.session"
        session_path.write_bytes(f"credential-{account_id}".encode())
        summaries.append(
            TdataAccountSummary(user_id=int(account_id), session_path=str(session_path)),
        )

    async def convert(
        _request: TdataConvertRequest,
        _staging_dir: Path,
    ) -> TdataConvertResult:
        return TdataConvertResult(status="ok", accounts=summaries)

    calls = 0

    async def check(request: AccountCheckRequest) -> TelegramSessionCheckResult:
        nonlocal calls
        calls += 1
        if calls == 2:
            msg = "second account check failed"
            raise RuntimeError(msg)
        account_id = request.account_id
        return TelegramSessionCheckResult(
            account_id=account_id,
            session_path=f"sessions/{account_id}",
            status="alive",
            is_temporary=False,
            user_id=int(account_id),
        )

    monkeypatch.setattr("services.accounts.sessions.convert_tdata_zip", convert)
    monkeypatch.setattr("services.accounts.sessions.check_telegram_session", check)


@pytest.mark.asyncio
async def test_tdata_rollback_keeps_cleaning_after_database_delete_failure(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """A best-effort DB rollback failure must not strand credential files."""
    first_id, second_id = "91001", "91002"
    _configure_failing_two_account_import(
        monkeypatch,
        tmp_path,
        first_id=first_id,
        second_id=second_id,
    )
    delete_attempts: list[str] = []

    async def unavailable_database(account_id: str) -> None:
        delete_attempts.append(account_id)
        msg = "database temporarily unavailable"
        raise RuntimeError(msg)

    monkeypatch.setattr("services.accounts._tdata.delete_account", unavailable_database)

    with pytest.raises(RuntimeError, match=r"^second account check failed$"):
        await import_account_tdata(TdataConvertRequest(filename="accounts.zip", content=b"zip"))

    assert delete_attempts == [first_id, second_id]
    assert not (settings.telegram.session_dir / f"{first_id}.session").exists()
    assert not (settings.telegram.session_dir / f"{second_id}.session").exists()
