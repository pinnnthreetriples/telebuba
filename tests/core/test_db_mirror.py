"""Database snapshots are mirrored encrypted; a plaintext database never reaches the mirror."""

from __future__ import annotations

import sqlite3
from contextlib import closing
from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING, cast

import pytest

from core import db_mirror
from core.config import settings
from core.db_snapshot import write_snapshot
from core.session_vault import SessionVaultError, new_vault_key
from tools import session_vault as cli

if TYPE_CHECKING:
    from pathlib import Path

    from sqlalchemy.engine import Connection

_SECRET = b"plaintext-marker-9f3"


def _fake_vacuum(_connection: Connection, path: Path) -> None:
    with closing(sqlite3.connect(path)) as db, db:
        db.execute("create table t (v blob)")
        db.execute("insert into t values (?)", (_SECRET,))


def _clock(minute: int):
    return lambda: datetime(2026, 11, 20, 10, minute, tzinfo=UTC)


@pytest.fixture
def mirror(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    monkeypatch.setattr(settings.session_vault, "key", new_vault_key())
    monkeypatch.setattr(settings.session_vault, "mirror_dir", tmp_path / "mirror")
    monkeypatch.setattr(settings.db, "backup_keep", 2)
    db_mirror.drain_mirror_errors()
    return tmp_path / "mirror" / "db"


def _snap(tmp_path: Path, minute: int) -> Path:
    return write_snapshot(
        cast("Connection", None),
        tmp_path / "backups",
        clock=_clock(minute),
        vacuum=_fake_vacuum,
    )


def test_a_snapshot_is_mirrored_encrypted_and_restorable(tmp_path: Path, mirror: Path) -> None:
    local = _snap(tmp_path, 1)

    copies = sorted(mirror.glob("telebuba-*.db.enc"))
    assert [c.name for c in copies] == [local.name + ".enc"]
    assert _SECRET not in copies[0].read_bytes()
    assert not list(mirror.glob("*.part"))
    restored = tmp_path / "restored.db"
    db_mirror.restore_db_copy(copies[0], restored)
    assert _SECRET in restored.read_bytes()
    with pytest.raises(SessionVaultError):  # never over an existing file
        db_mirror.restore_db_copy(copies[0], restored)


def test_the_mirror_is_pruned_to_the_local_keep_count(tmp_path: Path, mirror: Path) -> None:
    for minute in range(4):
        _snap(tmp_path, minute)

    names = [p.name for p in sorted(mirror.glob("*.db.enc"))]
    assert len(names) == 2
    assert names == [f"telebuba-20261120T10{m:02d}00000000Z.db.enc" for m in (2, 3)]


def test_without_the_key_or_a_mirror_nothing_is_written(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(settings.session_vault, "mirror_dir", tmp_path / "mirror")
    _snap(tmp_path, 1)  # no key: a plaintext copy must not go to the mirror
    assert not (tmp_path / "mirror").exists()
    monkeypatch.setattr(settings.session_vault, "key", new_vault_key())
    monkeypatch.setattr(settings.session_vault, "mirror_dir", None)
    _snap(tmp_path, 2)
    assert not (tmp_path / "mirror").exists()
    assert db_mirror.db_mirror_enabled() is False


@pytest.mark.usefixtures("mirror")
def test_a_mirror_failure_is_queued_and_the_local_snapshot_stands(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    def _boom(*_a: object, **_k: object) -> None:
        raise PermissionError

    monkeypatch.setattr(db_mirror, "_publish", _boom)

    local = _snap(tmp_path, 1)

    assert local.is_file()
    assert db_mirror.drain_mirror_errors() == ["PermissionError"]
    assert db_mirror.drain_mirror_errors() == []


@pytest.mark.usefixtures("mirror")
def test_newest_mirror_at_reads_names_and_ignores_the_future(tmp_path: Path) -> None:
    _snap(tmp_path, 1)
    _snap(tmp_path, 5)
    now = datetime(2026, 11, 20, 10, 3, tzinfo=UTC)

    assert db_mirror.newest_mirror_at(now=now) == datetime(2026, 11, 20, 10, 1, tzinfo=UTC)
    assert db_mirror.newest_mirror_at(now=now + timedelta(days=1)) == datetime(
        2026, 11, 20, 10, 5, tzinfo=UTC
    )


def test_the_cli_restores_a_database_copy_into_a_new_file(
    tmp_path: Path, mirror: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    _snap(tmp_path, 1)
    copy = next(mirror.glob("*.db.enc"))
    target = tmp_path / "out.db"

    assert cli.main(["restore-db", str(copy), str(target)]) == 0
    assert _SECRET in target.read_bytes()
    assert cli.main(["restore-db", str(copy), str(target)]) == 1
    assert "vault_target_exists" in capsys.readouterr().err
