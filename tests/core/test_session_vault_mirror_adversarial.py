"""Mirror reconciliation with real files: unplugged, deleted, extra versions, first-kept rule."""

from __future__ import annotations

import shutil
import sqlite3
from contextlib import closing
from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING

import pytest

from core import session_vault as vault
from core.config import settings

if TYPE_CHECKING:
    from pathlib import Path


def _session(path: Path, key: bytes) -> None:
    with closing(sqlite3.connect(path)) as db, db:
        db.execute("create table if not exists sessions (auth_key blob)")
        db.execute("delete from sessions")
        db.execute("insert into sessions values (?)", (key,))


def _at(i: int) -> datetime:
    return datetime(2026, 11, 16, 12, 0, tzinfo=UTC) + timedelta(minutes=i)


def _names(folder: Path) -> list[str]:
    return sorted(p.name for p in folder.glob("*.session.enc")) if folder.is_dir() else []


@pytest.fixture
def setup(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> tuple[Path, Path, Path]:
    monkeypatch.setattr(settings.session_vault, "key", vault.new_vault_key())
    monkeypatch.setattr(settings.session_vault, "mirror_dir", tmp_path / "mirror")
    monkeypatch.setattr(settings.session_vault, "keep_versions", 2)
    session = tmp_path / "acc.session"
    _session(session, b"k0")
    return session, settings.session_vault.dir / "acc", tmp_path / "mirror" / "acc"


def test_both_folders_keep_the_first_version_and_the_same_set(
    setup: tuple[Path, Path, Path],
) -> None:
    session, primary, mirror = setup
    for i in range(6):
        _session(session, f"k{i}".encode())
        vault.snapshot_session(session, now=_at(i))
    assert _names(primary) == _names(mirror)
    assert len(_names(primary)) == 3  # first + keep(2)
    assert _names(primary)[0].startswith(_at(0).strftime("%Y%m%dT%H%M%S"))


def test_a_mirror_deleted_entirely_is_rebuilt_even_when_the_file_is_unchanged(
    setup: tuple[Path, Path, Path],
) -> None:
    session, primary, mirror = setup
    for i in range(3):
        _session(session, f"k{i}".encode())
        vault.snapshot_session(session, now=_at(i))
    shutil.rmtree(mirror.parent)
    assert vault.snapshot_session(session, now=_at(9)) is None  # unchanged digest
    assert _names(mirror) == _names(primary)


def test_a_mirror_extra_version_the_primary_pruned_is_pruned_too(
    setup: tuple[Path, Path, Path],
) -> None:
    session, primary, mirror = setup
    for i in range(5):
        _session(session, f"k{i}".encode())
        vault.snapshot_session(session, now=_at(i))
    extra = mirror / f"{_at(1).strftime('%Y%m%dT%H%M%S%fZ')}-deadbeefdead.session.enc"
    extra.write_bytes(b"old")
    vault.snapshot_session(session, now=_at(10))
    assert _names(mirror) == _names(primary)


def test_a_failing_mirror_raises_but_never_costs_the_primary(
    setup: tuple[Path, Path, Path], tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    session, primary, _mirror = setup
    vault.snapshot_session(session, now=_at(0))
    before = _names(primary)
    blocker = tmp_path / "blocked"
    blocker.write_bytes(b"i am a file, not a folder")
    monkeypatch.setattr(settings.session_vault, "mirror_dir", blocker)
    _session(session, b"k1")
    # The exact OSError subclass for "a file where a folder is expected" differs by platform.
    with pytest.raises(OSError):  # noqa: PT011
        vault.snapshot_session(session, now=_at(1))
    after = _names(primary)
    assert set(before) <= set(after)
    assert len(after) == len(before) + 1  # the new primary version is kept


def test_the_unplugged_mirror_catches_up_with_every_missed_version(
    setup: tuple[Path, Path, Path], tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    session, primary, mirror = setup
    real = settings.session_vault.mirror_dir
    blocker = tmp_path / "blocked"
    blocker.write_bytes(b"x")
    monkeypatch.setattr(settings.session_vault, "mirror_dir", blocker)
    monkeypatch.setattr(settings.session_vault, "keep_versions", 10)
    for i in range(3):
        _session(session, f"k{i}".encode())
        with pytest.raises(OSError):  # noqa: PT011 - platform-dependent subclass, as above
            vault.snapshot_session(session, now=_at(i))
    monkeypatch.setattr(settings.session_vault, "mirror_dir", real)
    vault.snapshot_session(session, now=_at(5))
    assert _names(mirror) == _names(primary)
    assert not list(mirror.glob("*.part"))
