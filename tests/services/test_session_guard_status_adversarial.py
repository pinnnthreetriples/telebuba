"""The /session-guard backups block under hostile conditions, and what it costs per poll."""

from __future__ import annotations

import sqlite3
from contextlib import closing
from pathlib import Path

import pytest

from core.config import settings
from core.session_vault import new_vault_key, snapshot_session
from schemas.accounts import AccountCreate
from services import session_guard
from services.accounts import add_account
from services.session_guard import _backup_status


@pytest.fixture(autouse=True)
def _no_vault(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setattr(settings.passkey_vault, "token", "")
    monkeypatch.setattr(settings.db, "backup_dir", tmp_path / "backups")


def _session(name: str) -> Path:
    path = settings.telegram.session_dir / f"{name}.session"
    path.parent.mkdir(parents=True, exist_ok=True)
    with closing(sqlite3.connect(path)) as db, db:
        db.execute("create table if not exists sessions (auth_key blob)")
        db.execute("insert into sessions values (?)", (b"k",))
    return path


@pytest.mark.asyncio
async def test_an_unreadable_vault_folder_never_raises_into_the_endpoint(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(settings.session_vault, "key", new_vault_key())
    await add_account(AccountCreate(account_id="acc-1"))
    settings.session_vault.dir.mkdir(parents=True, exist_ok=True)

    def _denied(_self: Path) -> object:
        raise PermissionError

    monkeypatch.setattr(Path, "iterdir", _denied)
    status = await session_guard.read_session_guard_status()  # must not raise
    assert status.backups.accounts_total == 1


@pytest.mark.asyncio
async def test_an_unreadable_backup_dir_never_raises_into_the_endpoint(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def _denied(_dir: object) -> None:
        raise PermissionError

    monkeypatch.setattr(_backup_status, "newest_backup_at", _denied)
    status = await session_guard.read_session_guard_status()  # must not raise
    assert status.backups.db_backup_enabled is True


@pytest.mark.asyncio
async def test_poll_cost_for_forty_accounts_is_linear_and_small(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(settings.session_vault, "key", new_vault_key())
    for i in range(40):
        await add_account(AccountCreate(account_id=f"acc-{i}"))
        snapshot_session(_session(f"acc-{i}"))
    calls = {"glob": 0, "iterdir": 0, "is_dir": 0}
    real = {n: getattr(Path, n) for n in calls}

    def _wrap(name: str) -> object:
        def _inner(self: Path, *a: object, **k: object) -> object:
            calls[name] += 1
            return real[name](self, *a, **k)

        return _inner

    for name in calls:
        monkeypatch.setattr(Path, name, _wrap(name))
    status = await session_guard.read_session_guard_status()
    print("FS CALLS PER POLL (40 accounts):", calls)  # noqa: T201
    assert status.backups.accounts_without_copy == 0
    assert calls["glob"] <= 4 * 40 + 10
