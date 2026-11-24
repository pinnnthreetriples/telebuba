"""Session copies are made when a key is written and once a day; removals stay recoverable."""

from __future__ import annotations

import asyncio
import sqlite3
from contextlib import closing
from typing import TYPE_CHECKING

import pytest

from core.config import settings
from core.logging import log_event
from core.session_vault import list_versions, new_vault_key
from core.telegram_client import back_up_account_session as real_back_up
from schemas.accounts import AccountCreate
from services import session_backups
from services.accounts import add_account, remove_account

if TYPE_CHECKING:
    from collections.abc import Iterator
    from pathlib import Path

    from schemas.logs import LogEntry


def _write_session(name: str, key: bytes = b"k1") -> Path:
    path = settings.telegram.session_dir / f"{name}.session"
    path.parent.mkdir(parents=True, exist_ok=True)
    with closing(sqlite3.connect(path)) as db, db:
        db.execute("create table if not exists sessions (auth_key blob)")
        db.execute("delete from sessions")
        db.execute("insert into sessions values (?)", (key,))
    return path


@pytest.fixture(autouse=True)
def _fresh() -> Iterator[None]:
    session_backups.reset_for_tests()
    yield
    session_backups.reset_for_tests()


@pytest.fixture
def on(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(settings.session_vault, "key", new_vault_key())


@pytest.mark.asyncio
async def test_with_no_key_the_pass_copies_nothing_and_says_so_once(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    logged: list[str] = []

    async def _log(_level: str, event: str, **_kwargs: object) -> None:
        logged.append(event)

    monkeypatch.setattr(session_backups, "log_event", _log)
    await add_account(AccountCreate(account_id="acc-1"))
    _write_session("acc-1")

    assert await session_backups.run_backup_pass() == 0
    assert await session_backups.run_backup_pass() == 0
    assert logged == ["session_vault_off"]


@pytest.mark.usefixtures("on")
@pytest.mark.asyncio
async def test_the_pass_copies_each_account_once_per_interval() -> None:
    await add_account(AccountCreate(account_id="acc-1"))
    _write_session("acc-1")

    assert await session_backups.run_backup_pass() == 1
    _write_session("acc-1", b"changed")  # changed, but not due again yet
    assert await session_backups.run_backup_pass() == 0
    assert len(list_versions("acc-1")) == 1


@pytest.mark.usefixtures("on")
@pytest.mark.asyncio
async def test_a_sign_in_copies_the_new_key_at_once() -> None:
    await add_account(AccountCreate(account_id="acc-1"))
    _write_session("acc-1")
    await session_backups.start_session_backups()

    await log_event("INFO", "passkey_login_success", account_id="acc-1")
    for _ in range(100):
        if list_versions("acc-1"):
            break
        await asyncio.sleep(0.01)
    await session_backups.shutdown_session_backups()

    assert len(list_versions("acc-1")) == 1


@pytest.mark.asyncio
async def test_deleting_an_account_keeps_its_session_in_the_quarantine() -> None:
    await add_account(AccountCreate(account_id="acc-1"))
    session = _write_session("acc-1")

    await remove_account("acc-1")

    assert not session.exists()
    kept = list(settings.session_vault.quarantine_dir.glob("*-acc-1.session"))
    assert len(kept) == 1


def _capture_log(monkeypatch: pytest.MonkeyPatch) -> list[tuple[str, str | None]]:
    logged: list[tuple[str, str | None]] = []

    async def _log(
        _level: str, event: str, *, account_id: str | None = None, **_kwargs: object
    ) -> None:
        logged.append((event, account_id))

    monkeypatch.setattr(session_backups, "log_event", _log)
    return logged


@pytest.mark.usefixtures("on")
@pytest.mark.asyncio
async def test_a_failed_copy_is_retried_after_the_retry_delay_not_every_pass_nor_a_day_later(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _capture_log(monkeypatch)
    calls: list[int] = []

    async def _fails_once(account_id: str, session_name: str | None = None) -> bool:
        calls.append(1)
        if len(calls) == 1:
            msg = "mirror disk is away"
            raise OSError(msg)
        return await real_back_up(account_id, session_name)

    monkeypatch.setattr(session_backups, "back_up_account_session", _fails_once)
    await add_account(AccountCreate(account_id="acc-1"))
    _write_session("acc-1")

    assert await session_backups.run_backup_pass() == 0  # the copy raised
    assert await session_backups.run_backup_pass() == 0  # inside the retry delay: not tried
    assert len(calls) == 1

    monkeypatch.setattr(settings.session_vault, "retry_minutes", 1e-9)
    assert await session_backups.run_backup_pass() == 1  # retried, and not a day later
    assert len(list_versions("acc-1")) == 1
    assert await session_backups.run_backup_pass() == 0  # now copied: the daily interval rules
    assert len(calls) == 2


@pytest.mark.usefixtures("on")
@pytest.mark.asyncio
async def test_one_failing_file_keeps_the_account_uncopied(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    logged = _capture_log(monkeypatch)

    async def _second_device_fails(account_id: str, session_name: str | None = None) -> bool:
        if session_name == "acc-1-b":
            msg = "unreadable"
            raise OSError(msg)
        return await real_back_up(account_id, session_name)

    monkeypatch.setattr(session_backups, "back_up_account_session", _second_device_fails)
    account = await add_account(AccountCreate(account_id="acc-1"))
    _write_session("acc-1")
    account = account.model_copy(update={"second_session_name": "acc-1-b"})

    assert await session_backups.back_up_account(account) == 1  # the primary did go in
    assert "acc-1" not in session_backups._state.copied
    assert logged == [("session_backup_failed", "acc-1")]


@pytest.mark.usefixtures("on")
@pytest.mark.asyncio
async def test_a_failed_copy_after_a_sign_in_is_retried_by_the_pass(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _capture_log(monkeypatch)
    calls: list[int] = []

    async def _fails_once(account_id: str, session_name: str | None = None) -> bool:
        calls.append(1)
        if len(calls) == 1:
            msg = "disk full"
            raise OSError(msg)
        return await real_back_up(account_id, session_name)

    monkeypatch.setattr(session_backups, "back_up_account_session", _fails_once)
    monkeypatch.setattr(settings.session_vault, "retry_minutes", 1e-9)
    await add_account(AccountCreate(account_id="acc-1"))
    _write_session("acc-1")

    await session_backups._after_write("acc-1")
    assert list_versions("acc-1") == []

    assert await session_backups.run_backup_pass() == 1  # not parked for 24 h
    assert len(list_versions("acc-1")) == 1


@pytest.mark.usefixtures("on")
@pytest.mark.asyncio
async def test_an_unexpected_error_after_a_sign_in_is_logged_and_the_subscriber_goes_on(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    logged = _capture_log(monkeypatch)
    real_fetch = session_backups.fetch_account
    calls: list[int] = []

    async def _fetch_breaks_once(account_id: str) -> object:
        calls.append(1)
        if len(calls) == 1:
            msg = "database is locked"
            raise RuntimeError(msg)
        return await real_fetch(account_id)

    monkeypatch.setattr(session_backups, "fetch_account", _fetch_breaks_once)
    await add_account(AccountCreate(account_id="acc-1"))
    _write_session("acc-1")
    await session_backups.start_session_backups()

    await log_event("INFO", "passkey_login_success", account_id="acc-1")
    await log_event("INFO", "passkey_login_success", account_id="acc-1")
    for _ in range(200):
        if list_versions("acc-1"):
            break
        await asyncio.sleep(0.01)
    task = session_backups._state.task
    assert task is not None
    assert not task.done()
    await session_backups.shutdown_session_backups()

    assert ("session_backup_failed", "acc-1") in logged
    assert len(list_versions("acc-1")) == 1  # the second sign-in was still served


@pytest.mark.usefixtures("on")
@pytest.mark.asyncio
async def test_the_subscriber_survives_an_error_while_scheduling(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    logged = _capture_log(monkeypatch)
    real_schedule = session_backups._schedule
    seen: list[int] = []

    def _schedule_breaks_once(entry: LogEntry) -> None:
        seen.append(1)
        if len(seen) == 1:
            msg = "boom"
            raise RuntimeError(msg)
        real_schedule(entry)

    monkeypatch.setattr(session_backups, "_schedule", _schedule_breaks_once)
    await add_account(AccountCreate(account_id="acc-1"))
    _write_session("acc-1")
    await session_backups.start_session_backups()

    await log_event("INFO", "passkey_login_success", account_id="acc-1")
    await log_event("INFO", "passkey_login_success", account_id="acc-1")
    for _ in range(200):
        if list_versions("acc-1"):
            break
        await asyncio.sleep(0.01)
    await session_backups.shutdown_session_backups()

    assert ("session_backup_failed", None) in logged
    assert len(list_versions("acc-1")) == 1
