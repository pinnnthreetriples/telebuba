"""Encrypted session copies: versioned, deduplicated, restorable; removals go to quarantine."""

from __future__ import annotations

import sqlite3
from contextlib import closing
from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING

import pytest

from core import session_vault as vault
from core.config import settings
from tools import session_vault as cli

if TYPE_CHECKING:
    from pathlib import Path


def _session(path: Path, key: bytes) -> None:
    """A real SQLite file shaped like Telethon's, holding ``key``."""
    with closing(sqlite3.connect(path)) as db, db:
        db.execute("create table if not exists sessions (auth_key blob)")
        db.execute("delete from sessions")
        db.execute("insert into sessions values (?)", (key,))


def _key_of(path: Path) -> bytes:
    with closing(sqlite3.connect(path)) as db:
        return bytes(db.execute("select auth_key from sessions").fetchone()[0])


@pytest.fixture
def on(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(settings.session_vault, "key", vault.new_vault_key())


def _at(minute: int) -> datetime:
    return datetime(2026, 11, 16, 12, minute, tzinfo=UTC)


def test_with_no_key_nothing_is_copied(tmp_path: Path) -> None:
    session = tmp_path / "acc.session"
    _session(session, b"k1")

    assert vault.snapshot_session(session) is None
    assert not settings.session_vault.dir.exists()


@pytest.mark.usefixtures("on")
def test_a_copy_is_encrypted_restorable_and_never_overwrites(tmp_path: Path) -> None:
    session = tmp_path / "acc.session"
    _session(session, b"the-auth-key")

    copy = vault.snapshot_session(session, now=_at(0))

    assert copy is not None
    assert b"the-auth-key" not in copy.read_bytes()
    target = tmp_path / "restored.session"
    vault.restore_session(copy, target)
    assert _key_of(target) == b"the-auth-key"
    with pytest.raises(vault.SessionVaultError) as refused:
        vault.restore_session(copy, target)
    assert refused.value.code == "vault_target_exists"


@pytest.mark.usefixtures("on")
def test_an_unchanged_file_is_not_copied_again_and_old_versions_are_pruned_but_the_first(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(settings.session_vault, "keep_versions", 2)
    session = tmp_path / "acc.session"
    _session(session, b"k0")
    first = vault.snapshot_session(session, now=_at(0))
    assert vault.snapshot_session(session, now=_at(1)) is None  # unchanged

    for minute in range(1, 5):
        _session(session, f"k{minute}".encode())
        vault.snapshot_session(session, now=_at(minute))

    versions = vault.list_versions("acc")
    assert versions[0] == first  # the account as it was bought stays
    assert len(versions) == 3


@pytest.mark.usefixtures("on")
def test_every_copy_also_goes_to_the_mirror(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    mirror = tmp_path / "mirror"
    monkeypatch.setattr(settings.session_vault, "mirror_dir", mirror)
    session = tmp_path / "acc.session"
    _session(session, b"k1")

    copy = vault.snapshot_session(session, now=_at(0))

    assert copy is not None
    assert (mirror / "acc" / copy.name).read_bytes() == copy.read_bytes()


def test_a_copy_made_with_another_key_is_refused(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(settings.session_vault, "key", vault.new_vault_key())
    session = tmp_path / "acc.session"
    _session(session, b"k1")
    copy = vault.snapshot_session(session)
    assert copy is not None
    monkeypatch.setattr(settings.session_vault, "key", vault.new_vault_key())

    with pytest.raises(vault.SessionVaultError) as refused:
        vault.restore_session(copy, tmp_path / "x.session")

    assert refused.value.code == "vault_copy_unreadable"


def test_a_removed_file_is_quarantined_and_purged_only_after_the_window(tmp_path: Path) -> None:
    session = tmp_path / "acc.session"
    _session(session, b"k1")
    moved_at = datetime.now(UTC)

    kept = vault.quarantine_file(session, now=moved_at)

    assert kept is not None
    assert not session.exists()
    assert _key_of(kept) == b"k1"
    later = moved_at + timedelta(days=settings.session_vault.quarantine_days)
    assert vault.purge_quarantine(now=(later - timedelta(hours=1)).timestamp()) == 0
    assert vault.purge_quarantine(now=(later + timedelta(hours=1)).timestamp()) == 1
    assert not kept.exists()


def test_the_cli_makes_a_key_and_restores(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    assert cli.main(["new-key"]) == 0
    key = capsys.readouterr().out.strip()
    monkeypatch.setattr(settings.session_vault, "key", key)
    session = tmp_path / "acc.session"
    _session(session, b"k1")
    copy = vault.snapshot_session(session)
    assert copy is not None

    assert cli.main(["list", "acc"]) == 0
    assert copy.name in capsys.readouterr().out
    assert cli.main(["restore", str(copy), str(tmp_path / "back.session")]) == 0
    assert cli.main(["restore", str(copy), str(tmp_path / "back.session")]) == 1
    assert _key_of(tmp_path / "back.session") == b"k1"
