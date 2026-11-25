"""Adversarial probes of recovery: newer schema, DB mirror, bundles, restore, vault snapshot."""

from __future__ import annotations

import asyncio
import sqlite3
from contextlib import closing
from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING

import pytest

from core import db_mirror
from core import session_vault as vault
from core.config import settings
from core.db import _get_engine, configure_database, dispose_engine  # type: ignore[attr-defined]
from core.db_snapshot import prepare_schema
from core.migrations import MIGRATIONS, SchemaNewerThanCodeError
from core.passkey_vault import PasskeyVaultError
from core.passkey_vault_snapshot import take_vault_snapshot
from core.session_file import inspect_session_file

if TYPE_CHECKING:
    from pathlib import Path

_NEWEST = max(v for v, _n, _b in MIGRATIONS)


def _dump(path: Path) -> list[str]:
    with closing(sqlite3.connect(path)) as c:
        return list(c.iterdump())


def _session(path: Path, key: bytes) -> None:
    with closing(sqlite3.connect(path)) as db, db:
        db.execute("create table if not exists sessions (server_address text, auth_key blob)")
        db.execute("delete from sessions")
        db.execute("insert into sessions values ('dc', ?)", (key,))


@pytest.fixture
def on(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    monkeypatch.setattr(settings.session_vault, "key", vault.new_vault_key())
    monkeypatch.setattr(settings.session_vault, "mirror_dir", tmp_path / "mirror")
    return tmp_path


# The first build must take the "no tables" branch of the snapshot check, not a copy's.
@pytest.mark.fresh_database
def test_newer_db_is_byte_identical_and_no_snapshot_taken(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(settings.db, "backup_dir", tmp_path / "backups")
    monkeypatch.setattr(settings.db, "backup_enabled", True)
    configure_database(tmp_path / "x.db")
    try:
        engine = _get_engine()
        with closing(sqlite3.connect(tmp_path / "x.db")) as c, c:
            c.execute(
                "INSERT INTO schema_version(version,name,applied_at) VALUES (?, 'x','x')",
                (_NEWEST + 3,),
            )
        before = _dump(tmp_path / "x.db")
        created: list[bool] = []
        with pytest.raises(SchemaNewerThanCodeError):
            prepare_schema(engine, lambda _e: created.append(True))
        after = _dump(tmp_path / "x.db")
    finally:
        dispose_engine()
    assert before == after
    assert created == []
    assert not (tmp_path / "backups").exists() or not list((tmp_path / "backups").iterdir())


def test_meta_file_is_never_a_version(on: Path) -> None:
    session = on / "acc.session"
    _session(session, b"k0")
    v1 = vault.snapshot_session(session, meta=b'{"a":1}')
    assert v1 is not None
    folder = v1.parent
    (folder / "zzzz.session.enc.meta").write_bytes(b"x")  # sorts last
    (folder / "zzzz.session.enc.meta.part").write_bytes(b"x")
    (folder / "zzzz.session.enc.part").write_bytes(b"x")
    assert vault.list_versions("acc") == [v1]
    assert vault.session_copies("acc")[0] == 1
    assert vault.copy_overview(["acc"]).without_copy == 0
    assert vault.snapshot_session(session, meta=b'{"a":2}') is None


def test_a_bundle_write_failure_does_not_fail_the_copy_or_skip_mirror(
    on: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    session = on / "acc.session"
    _session(session, b"k0")
    real = vault._write_version

    def flaky(folder: Path, name: str, payload: bytes) -> Path:
        if name.endswith(".meta") and "mirror" not in str(folder):
            msg = "disk full"
            raise OSError(msg)
        return real(folder, name, payload)

    monkeypatch.setattr(vault, "_write_version", flaky)
    vault.snapshot_session(session, meta=b"{}")  # must not raise
    assert list((on / "mirror" / "acc").glob("*.session.enc"))


def test_mirror_dir_is_a_file_never_raises_and_is_reported(on: Path) -> None:
    (on / "mirror").write_text("i am a file")
    snap = on / "telebuba-20261120T100000000000Z.db"
    snap.write_bytes(b"SQLite format 3\x00 plaintext-marker")
    db_mirror.drain_mirror_errors()
    assert db_mirror.mirror_snapshot(snap) is None
    assert db_mirror.drain_mirror_errors()
    assert snap.exists()


def test_key_rotation_status_and_prune_do_not_crash(
    on: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    for i in range(4):
        snap = on / f"telebuba-20261120T10{i:02d}00000000Z.db"
        snap.write_bytes(b"data")
        db_mirror.mirror_snapshot(snap)
    monkeypatch.setattr(settings.session_vault, "key", vault.new_vault_key())
    snap = on / "telebuba-20261120T110000000000Z.db"
    snap.write_bytes(b"data2")
    assert db_mirror.mirror_snapshot(snap) is not None
    assert db_mirror.newest_mirror_at(now=datetime(2027, 1, 1, tzinfo=UTC)) is not None
    old = sorted((on / "mirror" / "db").glob("*.db.enc"))[0]
    with pytest.raises(vault.SessionVaultError):
        db_mirror.restore_db_copy(old, on / "out.db")
    assert not (on / "out.db").exists()


def test_restore_db_refuses_existing_target_and_copy_is_ciphertext(on: Path) -> None:
    snap = on / "telebuba-20261120T100000000000Z.db"
    snap.write_bytes(b"data")
    copy = db_mirror.mirror_snapshot(snap)
    assert copy is not None
    target = on / "t.db"
    target.write_bytes(b"keep")
    with pytest.raises(vault.SessionVaultError):
        db_mirror.restore_db_copy(copy, target)
    assert target.read_bytes() == b"keep"
    assert b"data" not in copy.read_bytes()


def test_future_mirror_names_are_ignored(on: Path) -> None:
    folder = on / "mirror" / "db"
    folder.mkdir(parents=True)
    future = datetime.now(UTC) + timedelta(days=3)
    (folder / f"telebuba-{future.strftime('%Y%m%dT%H%M%S%fZ')}.db.enc").write_bytes(b"x")
    assert db_mirror.newest_mirror_at() is None


def test_failed_restore_leaves_no_half_written_session(on: Path) -> None:
    session = on / "acc.session"
    _session(session, b"k0")
    version = vault.snapshot_session(session)
    assert version is not None
    version.write_bytes(vault.vault_cipher().encrypt(b"not a sqlite database at all"))
    target = on / "restored.session"
    with pytest.raises(Exception):  # noqa: B017, PT011
        vault.restore_session(version, target)
    assert not target.exists() or inspect_session_file(target) in {"ok", "no_key"}


@pytest.mark.asyncio
async def test_copy_meta_is_reset_when_the_copy_is_cancelled(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from schemas.accounts import AccountRead  # noqa: PLC0415
    from services import session_backups as sb  # noqa: PLC0415

    async def bundle(_a: object) -> bytes:
        return b"A"

    async def cancelled(*_a: object) -> bool:
        raise asyncio.CancelledError

    monkeypatch.setattr(sb, "build_recovery_bundle", bundle)
    monkeypatch.setattr(sb, "back_up_account_session", cancelled)
    account = AccountRead(account_id="a", status="alive", created_at="n", updated_at="n")
    with pytest.raises(asyncio.CancelledError):
        await sb.back_up_account(account)
    assert vault.copy_meta.get() is None


@pytest.mark.asyncio
async def test_token_not_in_exception_chain(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    async def handle(_r: asyncio.StreamReader, w: asyncio.StreamWriter) -> None:
        w.close()

    server = await asyncio.start_server(handle, "127.0.0.1", 0)
    port = server.sockets[0].getsockname()[1]
    monkeypatch.setattr(settings.passkey_vault, "addr", f"http://127.0.0.1:{port}")
    monkeypatch.setattr(settings.passkey_vault, "snapshot_token", "s.SECRETTOKEN\nx")
    monkeypatch.setattr(settings.db, "backup_dir", tmp_path / "b")
    try:
        with pytest.raises(PasskeyVaultError) as caught:
            await take_vault_snapshot()
    finally:
        server.close()
    chain = repr(caught.value) + str(caught.value) + repr(caught.value.__cause__)
    assert "SECRETTOKEN" not in chain
