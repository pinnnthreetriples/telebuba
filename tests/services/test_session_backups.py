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
from schemas.accounts import AccountCreate
from services import session_backups
from services.accounts import add_account, remove_account

if TYPE_CHECKING:
    from collections.abc import Iterator
    from pathlib import Path


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
