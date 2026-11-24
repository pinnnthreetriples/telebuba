"""Restoring an account's session file from the newest vault copy."""

from __future__ import annotations

import sqlite3
from contextlib import closing
from typing import TYPE_CHECKING

import pytest
import pytest_asyncio

from core.config import settings
from core.db import fetch_account
from core.session_vault import list_versions, new_vault_key, snapshot_session
from schemas.accounts import AccountCreate
from services.accounts import add_account
from services.accounts import session_restore as restore_module
from services.accounts._result import AccountActionError
from services.accounts.session_restore import restore_account_session, session_copies_of

if TYPE_CHECKING:
    from pathlib import Path


def _file(name: str = "acc-1") -> Path:
    return settings.telegram.session_dir / f"{name}.session"


def _write(path: Path, key: bytes | None) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with closing(sqlite3.connect(path)) as db, db:
        db.execute("create table if not exists sessions (server_address text, auth_key blob)")
        db.execute("delete from sessions")
        if key is not None:
            db.execute("insert into sessions values ('dc', ?)", (key,))


def _key(path: Path) -> bytes | None:
    with closing(sqlite3.connect(path)) as db:
        row = db.execute("select auth_key from sessions").fetchone()
    return None if row is None else bytes(row[0])


@pytest_asyncio.fixture(autouse=True)
async def _account(monkeypatch: pytest.MonkeyPatch) -> None:
    await add_account(AccountCreate(account_id="acc-1", session_name="acc-1"))

    async def _checked(_data: object) -> object:
        return await fetch_account("acc-1")

    monkeypatch.setattr(restore_module, "check_account_session", _checked)


@pytest.fixture
def on(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(settings.session_vault, "key", new_vault_key())


def _copy(key: bytes = b"good-key") -> None:
    _write(_file(), key)
    assert snapshot_session(_file()) is not None


@pytest.mark.usefixtures("on")
@pytest.mark.asyncio
async def test_a_missing_file_is_restored_from_the_newest_copy() -> None:
    _copy()
    _file().unlink()
    copies = await session_copies_of("acc-1")
    assert (copies.count, copies.can_restore) == (1, True)

    account = await restore_account_session("acc-1")

    assert account is not None
    assert _key(_file()) == b"good-key"


@pytest.mark.usefixtures("on")
@pytest.mark.asyncio
async def test_a_keyless_file_is_set_aside_and_replaced() -> None:
    _copy()
    _write(_file(), None)

    await restore_account_session("acc-1")

    assert _key(_file()) == b"good-key"
    assert list(settings.session_vault.quarantine_dir.glob("*-acc-1.session"))


@pytest.mark.usefixtures("on")
@pytest.mark.asyncio
async def test_never_over_a_keyed_file() -> None:
    _copy()
    _write(_file(), b"live-key")
    assert (await session_copies_of("acc-1")).can_restore is False

    with pytest.raises(AccountActionError, match="session_restore_file_present"):
        await restore_account_session("acc-1")

    assert _key(_file()) == b"live-key"


@pytest.mark.usefixtures("on")
@pytest.mark.asyncio
async def test_no_copy_is_refused() -> None:
    with pytest.raises(AccountActionError, match="session_restore_no_copy"):
        await restore_account_session("acc-1")
    assert list_versions("acc-1") == []


@pytest.mark.asyncio
async def test_vault_off_is_refused() -> None:
    with pytest.raises(AccountActionError, match="session_vault_off"):
        await restore_account_session("acc-1")
    assert (await session_copies_of("acc-1")).vault_enabled is False
