"""Tests for the accounts service layer."""

from __future__ import annotations

from typing import TYPE_CHECKING

import pytest

from core.config import settings
from core.db import configure_database
from core.logging import reset_logging_for_tests, setup_logging
from schemas.accounts import (
    AccountCheckRequest,
    AccountCreate,
    AccountFilter,
    AccountSessionFileImport,
    AccountStatus,
)
from schemas.tdata import TdataAccountSummary, TdataConvertRequest, TdataConvertResult
from schemas.telegram_session import TelegramSessionCheckResult
from services.accounts import (
    add_account,
    check_account_session,
    import_account_session,
    import_account_tdata,
    load_accounts_table,
)

if TYPE_CHECKING:
    from collections.abc import Iterator
    from pathlib import Path


@pytest.fixture(autouse=True)
def _isolate_runtime(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> Iterator[None]:
    configure_database(tmp_path / "telebuba.db")
    monkeypatch.setattr(settings.telegram, "session_dir", tmp_path / "sessions")
    monkeypatch.setattr(settings.logging, "path", tmp_path / "debug.log")
    monkeypatch.setattr(settings.logging, "sentry_dsn", "")
    reset_logging_for_tests()
    setup_logging()
    yield
    reset_logging_for_tests()


@pytest.mark.asyncio
async def test_add_account_creates_fingerprint_and_table_row() -> None:
    account = await add_account(
        AccountCreate(account_id="account-1", label="Main", session_name="session-1"),
    )
    state = await load_accounts_table(AccountFilter())

    assert account.account_id == "account-1"
    assert state.summary.total == 1
    assert state.summary.never_checked == 1
    assert state.rows[0].label == "Main"
    assert state.rows[0].device != "-"
    # A freshly added account has not been checked yet -> warn (amber).
    assert state.rows[0].health == "warn"


@pytest.mark.asyncio
async def test_import_account_session_saves_file_and_creates_account() -> None:
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
    assert (
        settings.telegram.session_dir / "real-account.session"
    ).read_bytes() == b"sqlite session bytes"
    assert state.rows[0].label == "Real account"
    assert state.rows[0].session == "real-account"


@pytest.mark.asyncio
async def test_import_account_session_rejects_non_session_file() -> None:
    with pytest.raises(ValueError, match=r"\.session"):
        await import_account_session(
            AccountSessionFileImport(filename="not-session.txt", content=b"content"),
        )


@pytest.mark.asyncio
async def test_load_accounts_table_filters_query_and_status() -> None:
    await add_account(AccountCreate(account_id="one", label="Alpha"))
    await add_account(AccountCreate(account_id="two", label="Beta"))

    query_state = await load_accounts_table(AccountFilter(query="alp"))
    status_state = await load_accounts_table(AccountFilter(status="alive"))

    assert [row.account_id for row in query_state.rows] == ["one"]
    assert status_state.rows == []
    assert status_state.summary.total == 2


@pytest.mark.asyncio
async def test_import_account_tdata_registers_each_account_and_checks(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def fake_convert(_req: TdataConvertRequest, _dir: object) -> TdataConvertResult:
        return TdataConvertResult(
            status="ok",
            accounts=[
                TdataAccountSummary(
                    user_id=111,
                    session_path=str(settings.telegram.session_dir / "111.session"),
                ),
                TdataAccountSummary(
                    user_id=222,
                    session_path=str(settings.telegram.session_dir / "222.session"),
                ),
            ],
        )

    async def fake_check(_request: object) -> TelegramSessionCheckResult:
        request_account_id = getattr(_request, "account_id", "?")
        return TelegramSessionCheckResult(
            account_id=request_account_id,
            session_path=f"sessions/{request_account_id}",
            status="alive",
            is_temporary=False,
            user_id=int(request_account_id),
            username=f"user{request_account_id}",
        )

    monkeypatch.setattr("services.accounts.convert_tdata_zip", fake_convert)
    monkeypatch.setattr("services.accounts.check_telegram_session", fake_check)

    accounts = await import_account_tdata(
        TdataConvertRequest(filename="tdata.zip", content=b"x", label="My pool"),
    )

    assert [a.account_id for a in accounts] == ["111", "222"]
    assert all(a.status == "alive" for a in accounts)


@pytest.mark.asyncio
async def test_import_account_tdata_surfaces_conversion_failure(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def fake_convert(_req: TdataConvertRequest, _dir: object) -> TdataConvertResult:
        return TdataConvertResult(status="invalid_zip", error="bad header")

    monkeypatch.setattr("services.accounts.convert_tdata_zip", fake_convert)

    with pytest.raises(ValueError, match=r"invalid_zip"):
        await import_account_tdata(
            TdataConvertRequest(filename="tdata.zip", content=b"x"),
        )


@pytest.mark.asyncio
async def test_import_account_tdata_rejects_empty_payload(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def fake_convert(_req: TdataConvertRequest, _dir: object) -> TdataConvertResult:
        return TdataConvertResult(status="ok", accounts=[])

    monkeypatch.setattr("services.accounts.convert_tdata_zip", fake_convert)

    with pytest.raises(ValueError, match=r"no accounts"):
        await import_account_tdata(
            TdataConvertRequest(filename="tdata.zip", content=b"x"),
        )


@pytest.mark.asyncio
async def test_check_account_session_updates_status(monkeypatch: pytest.MonkeyPatch) -> None:
    await add_account(AccountCreate(account_id="account-2"))

    async def fake_check(_request: object) -> TelegramSessionCheckResult:
        return TelegramSessionCheckResult(
            account_id="account-2",
            session_path="sessions/account-2",
            status="alive",
            is_temporary=False,
            user_id=123,
            username="checked",
        )

    monkeypatch.setattr("services.accounts.check_telegram_session", fake_check)

    account = await check_account_session(AccountCheckRequest(account_id="account-2"))
    state = await load_accounts_table(AccountFilter(status="alive"))

    assert account.status == "alive"
    assert state.summary.alive == 1
    assert state.rows[0].telegram == "@checked"
    assert state.rows[0].health == "ok"


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("status", "expected_health"),
    [
        ("alive", "ok"),
        ("new", "warn"),
        ("flood_wait", "warn"),
        ("network_error", "warn"),
        ("proxy_error", "warn"),
        ("unknown_error", "warn"),
        ("unauthorized", "fail"),
        ("session_error", "fail"),
        ("account_error", "fail"),
    ],
)
async def test_health_taxonomy_matches_status(
    monkeypatch: pytest.MonkeyPatch,
    status: AccountStatus,
    expected_health: str,
) -> None:
    """Every AccountStatus maps to exactly one of ok / warn / fail."""
    await add_account(AccountCreate(account_id="acc-h"))
    if status == "new":
        state = await load_accounts_table(AccountFilter())
        assert state.rows[0].health == expected_health
        return

    async def fake_check(_request: object) -> TelegramSessionCheckResult:
        return TelegramSessionCheckResult(
            account_id="acc-h",
            session_path="sessions/acc-h",
            status=status,
            is_temporary=status not in {"alive", "unauthorized", "session_error", "account_error"},
        )

    monkeypatch.setattr("services.accounts.check_telegram_session", fake_check)
    await check_account_session(AccountCheckRequest(account_id="acc-h"))
    state = await load_accounts_table(AccountFilter())
    assert state.rows[0].health == expected_health
