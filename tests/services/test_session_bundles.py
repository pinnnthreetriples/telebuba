"""The recovery bundle rides with every session copy; deleting an account leaves a tombstone."""

from __future__ import annotations

import json
import sqlite3
from contextlib import closing
from typing import TYPE_CHECKING

import pytest

from core.config import settings
from core.db import set_account_twofa_password
from core.session_vault import list_versions, meta_path, new_vault_key, read_bundle
from schemas.accounts import AccountCreate
from services import session_backups
from services.accounts import _recovery_bundle as bundle_module
from services.accounts import add_account, lifecycle, remove_account

if TYPE_CHECKING:
    from collections.abc import Iterator
    from pathlib import Path


def _write_session(name: str) -> Path:
    path = settings.telegram.session_dir / f"{name}.session"
    path.parent.mkdir(parents=True, exist_ok=True)
    with closing(sqlite3.connect(path)) as db, db:
        db.execute("create table if not exists sessions (auth_key blob)")
        db.execute("insert into sessions values (?)", (b"k1",))
    return path


@pytest.fixture(autouse=True)
def _fresh() -> Iterator[None]:
    session_backups.reset_for_tests()
    yield
    session_backups.reset_for_tests()


@pytest.fixture
def on(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(settings.session_vault, "key", new_vault_key())


@pytest.mark.usefixtures("on")
@pytest.mark.asyncio
async def test_a_copy_carries_device_and_cloud_password() -> None:
    account = await add_account(AccountCreate(account_id="acc-1", session_name="acc-1"))
    await set_account_twofa_password("acc-1", "cloud-pw")
    _write_session("acc-1")

    assert await session_backups.run_backup_pass() == 1

    (version,) = list_versions("acc-1")
    bundle = json.loads(read_bundle(meta_path(version)))
    assert bundle["format"] == 1
    assert bundle["account_id"] == account.account_id
    assert bundle["cloud_password"] == "cloud-pw"
    assert bundle["device_fingerprints"]
    assert "api_id" in bundle["device_fingerprints"][0]
    assert "username" not in json.dumps(bundle["proxy"])


@pytest.mark.usefixtures("on")
@pytest.mark.asyncio
async def test_a_bundle_that_cannot_be_built_never_fails_the_copy(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    logged: list[tuple[str, object]] = []

    async def _log(_level: str, event: str, **kwargs: object) -> None:
        logged.append((event, kwargs.get("extra")))

    async def _boom(*_a: object, **_k: object) -> bytes:
        raise RuntimeError

    monkeypatch.setattr(session_backups, "log_event", _log)
    monkeypatch.setattr(session_backups, "build_recovery_bundle", _boom)
    await add_account(AccountCreate(account_id="acc-1", session_name="acc-1"))
    _write_session("acc-1")

    assert await session_backups.run_backup_pass() == 1

    (version,) = list_versions("acc-1")
    assert not meta_path(version).exists()
    assert logged == [("session_bundle_failed", {"error_type": "RuntimeError"})]


@pytest.mark.usefixtures("on")
@pytest.mark.asyncio
async def test_deleting_an_account_leaves_a_tombstone_beside_its_session() -> None:
    await add_account(AccountCreate(account_id="acc-1", session_name="acc-1"))
    await set_account_twofa_password("acc-1", "cloud-pw")
    _write_session("acc-1")

    await remove_account("acc-1")

    folder = settings.session_vault.quarantine_dir
    (tomb,) = folder.glob("*-acc-1.account")
    assert b"cloud-pw" not in tomb.read_bytes()
    bundle = json.loads(read_bundle(tomb))
    assert bundle["cloud_password"] == "cloud-pw"
    assert "deleted_at" in bundle
    assert list(folder.glob("*-acc-1.session"))


@pytest.mark.asyncio
async def test_a_tombstone_that_cannot_be_written_never_blocks_the_delete(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    logged: list[tuple[str, object]] = []

    async def _log(_level: str, event: str, **kwargs: object) -> None:
        logged.append((event, kwargs.get("extra")))

    def _boom(*_a: object, **_k: object) -> None:
        raise PermissionError

    monkeypatch.setattr(lifecycle, "log_event", _log)
    monkeypatch.setattr(lifecycle, "write_tombstone", _boom)
    await add_account(AccountCreate(account_id="acc-1", session_name="acc-1"))

    await remove_account("acc-1")

    assert ("account_tombstone_failed", {"error_type": "PermissionError"}) in logged
    assert ("account_removed", None) in logged


def test_the_bundle_module_names_its_format() -> None:
    assert bundle_module.BUNDLE_FORMAT == 1
