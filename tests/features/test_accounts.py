from __future__ import annotations

from typing import TYPE_CHECKING

import pytest

from core.db import configure_database
from features.accounts import (
    add_account,
    check_account_session,
    import_account_session,
    load_accounts_table,
)
from schemas.accounts import (
    AccountCheckRequest,
    AccountCreate,
    AccountFilter,
    AccountSessionFileImport,
)
from schemas.telegram_session import TelegramSessionCheckResult

if TYPE_CHECKING:
    from pathlib import Path


@pytest.mark.asyncio
async def test_add_account_creates_fingerprint_and_table_row(tmp_path: Path) -> None:
    configure_database(tmp_path / "telebuba.db")

    account = await add_account(
        AccountCreate(account_id="account-1", label="Main", session_name="session-1"),
    )
    state = await load_accounts_table(AccountFilter())

    assert account.account_id == "account-1"
    assert state.summary.total == 1
    assert state.summary.never_checked == 1
    assert state.rows[0].label == "Main"
    assert state.rows[0].device != "-"


@pytest.mark.asyncio
async def test_import_account_session_saves_file_and_creates_account(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    configure_database(tmp_path / "telebuba.db")
    session_dir = tmp_path / "sessions"
    monkeypatch.setattr("features.accounts.settings.telegram.session_dir", session_dir)

    account = await import_account_session(
        AccountSessionFileImport(
            filename="real-account.session",
            content=b"sqlite session bytes",
            label="Real account",
        ),
    )
    state = await load_accounts_table(AccountFilter())

    assert account.account_id == "real-account"
    assert account.session_name == "real-account"
    assert (session_dir / "real-account.session").read_bytes() == b"sqlite session bytes"
    assert state.rows[0].label == "Real account"
    assert state.rows[0].session == "real-account"


@pytest.mark.asyncio
async def test_import_account_session_rejects_non_session_file(tmp_path: Path) -> None:
    configure_database(tmp_path / "telebuba.db")

    with pytest.raises(ValueError, match=r"\.session"):
        await import_account_session(
            AccountSessionFileImport(filename="not-session.txt", content=b"content"),
        )


@pytest.mark.asyncio
async def test_load_accounts_table_filters_query_and_status(tmp_path: Path) -> None:
    configure_database(tmp_path / "telebuba.db")
    await add_account(AccountCreate(account_id="one", label="Alpha"))
    await add_account(AccountCreate(account_id="two", label="Beta"))

    query_state = await load_accounts_table(AccountFilter(query="alp"))
    status_state = await load_accounts_table(AccountFilter(status="alive"))

    assert [row.account_id for row in query_state.rows] == ["one"]
    assert status_state.rows == []
    assert status_state.summary.total == 2


@pytest.mark.asyncio
async def test_check_account_session_updates_status(tmp_path: Path, monkeypatch) -> None:
    configure_database(tmp_path / "telebuba.db")
    await add_account(AccountCreate(account_id="account-2"))

    async def fake_check(_request):
        return TelegramSessionCheckResult(
            account_id="account-2",
            session_path="sessions/account-2",
            status="alive",
            is_temporary=False,
            user_id=123,
            username="checked",
        )

    monkeypatch.setattr("features.accounts.check_telegram_session", fake_check)

    account = await check_account_session(AccountCheckRequest(account_id="account-2"))
    state = await load_accounts_table(AccountFilter(status="alive"))

    assert account.status == "alive"
    assert state.summary.alive == 1
    assert state.rows[0].telegram == "@checked"
