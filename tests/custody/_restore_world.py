"""Restoring the session file from the encrypted copy, on the custody world.

The account's file is missing or holds no key; the vault holds a copy with the key Telegram
still honours. A crash anywhere in the restore must leave that key recoverable (the copy is
never touched), no junk where the file goes, and the keyless leftover set aside, not deleted.
"""

from __future__ import annotations

import sqlite3
from contextlib import closing
from typing import TYPE_CHECKING

from core import session_vault
from core.config import settings
from core.session_file import inspect_session_file
from core.session_vault import list_versions, new_vault_key, snapshot_session, vault_cipher
from core.telegram_client import _session_restore
from services.accounts import session_restore
from tests.custody._flows import seed_account
from tests.custody._world import OLD, World, read_key
from tests.session_files import write_session_file

if TYPE_CHECKING:
    from pathlib import Path

    import pytest

    from tests.custody._flows import Steps

# The same two initial states take the same steps: the leftover removal is a no-op on no file.
STEPS: Steps = [
    ("file.quarantine_leftover", False),
    ("vault.staged", "thread"),
    ("vault.restore", "thread"),
]


def vault_key_of_newest() -> bytes | None:
    """The auth key inside the newest encrypted copy, read with the vault key; ``None`` if none."""
    versions = list_versions("acc")
    if not versions:
        return None
    data = vault_cipher().decrypt(versions[-1].read_bytes())
    with closing(sqlite3.connect(":memory:")) as db:
        db.deserialize(data)
        row = db.execute("select auth_key from sessions").fetchone()
    return bytes(row[0]) if row else None


async def start(world: World, monkeypatch: pytest.MonkeyPatch, *, initial: str) -> None:
    """The vault holds a copy of the live key; the file is ``missing`` or ``keyless``."""
    monkeypatch.setattr(settings.session_vault, "key", new_vault_key())
    await seed_account(world)
    assert snapshot_session(world.file("acc")) is not None
    world.file("acc").unlink()
    if initial == "keyless":
        write_session_file(world.file("acc"), key=None)

    real_remove = _session_restore._remove_session_file
    real_restore = _session_restore.restore_session
    real_private = session_vault.make_private_file

    async def remove(path: str) -> None:
        await world.step("file.quarantine_leftover", lambda: real_remove(path))

    def restore(version: Path, target: Path) -> None:
        world.sync_step("vault.restore", lambda: real_restore(version, target))

    def private(path: Path) -> None:
        world.sync_step("vault.staged", lambda: real_private(path))

    async def checked(_data: object) -> None:
        return None

    monkeypatch.setattr(_session_restore, "_remove_session_file", remove)
    monkeypatch.setattr(_session_restore, "restore_session", restore)
    monkeypatch.setattr(session_vault, "make_private_file", private)
    monkeypatch.setattr(session_restore, "check_account_session", checked)
    monkeypatch.setattr(session_restore, "log_event", world.log_stub())


async def restore_flow() -> object:
    return await session_restore.restore_account_session("acc")


def assert_nothing_is_lost(world: World, *, initial: str, when: str) -> None:
    """I1 and I3: the key is recoverable, nothing junk is in the file's place, leftovers kept."""
    own = read_key(world.file("acc"))
    assert own == OLD or vault_key_of_newest() == OLD, f"I1 ({when}): the key is not recoverable"
    state = inspect_session_file(world.file("acc"))
    assert state in {"missing", "no_key", "ok"}, f"I3 ({when}): junk in the file's place: {state}"
    if initial == "keyless" and state == "missing":
        quarantined = list(settings.session_vault.quarantine_dir.glob("*-acc.session"))
        assert quarantined, f"I3 ({when}): the keyless leftover is gone, not set aside"
