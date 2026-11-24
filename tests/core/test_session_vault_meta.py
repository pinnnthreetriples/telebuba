"""A recovery bundle travels with each session copy; a tombstone sits in the quarantine."""

from __future__ import annotations

import json
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

_BUNDLE = json.dumps(
    {"account_id": "a", "cloud_password": "hunter2", "pending_cloud_password": None}
)


def _session(path: Path, key: bytes) -> None:
    with closing(sqlite3.connect(path)) as db, db:
        db.execute("create table if not exists sessions (auth_key blob)")
        db.execute("delete from sessions")
        db.execute("insert into sessions values (?)", (key,))


def _at(minute: int) -> datetime:
    return datetime(2026, 11, 16, 12, minute, tzinfo=UTC)


@pytest.fixture
def on(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setattr(settings.session_vault, "key", vault.new_vault_key())
    monkeypatch.setattr(settings.session_vault, "mirror_dir", tmp_path / "mirror")
    monkeypatch.setattr(settings.session_vault, "keep_versions", 2)


@pytest.mark.usefixtures("on")
def test_a_bundle_is_stored_encrypted_next_to_its_version_and_mirrored(tmp_path: Path) -> None:
    session = tmp_path / "acc.session"
    _session(session, b"k1")

    version = vault.snapshot_session(session, now=_at(0), meta=_BUNDLE.encode())

    assert version is not None
    bundle = vault.meta_path(version)
    assert b"hunter2" not in bundle.read_bytes()
    assert json.loads(vault.read_bundle(bundle))["cloud_password"] == "hunter2"
    assert (tmp_path / "mirror" / "acc" / bundle.name).is_file()
    # The bundle is never taken for a version.
    assert vault.list_versions("acc") == [version]
    assert vault.copy_overview(["acc"]).without_copy == 0


@pytest.mark.usefixtures("on")
def test_an_unchanged_session_refreshes_the_newest_bundle(tmp_path: Path) -> None:
    session = tmp_path / "acc.session"
    _session(session, b"k1")
    version = vault.snapshot_session(session, now=_at(0), meta=b'{"v": 1}')
    assert version is not None

    assert vault.snapshot_session(session, now=_at(1), meta=b'{"v": 2}') is None

    assert json.loads(vault.read_bundle(vault.meta_path(version))) == {"v": 2}
    mirrored = tmp_path / "mirror" / "acc" / vault.meta_path(version).name
    assert json.loads(vault.read_bundle(mirrored)) == {"v": 2}


@pytest.mark.usefixtures("on")
def test_a_bundle_is_pruned_with_its_version(tmp_path: Path) -> None:
    session = tmp_path / "acc.session"
    versions = []
    for minute in range(5):
        _session(session, f"k{minute}".encode())
        versions.append(vault.snapshot_session(session, now=_at(minute), meta=b"{}"))

    kept = vault.list_versions("acc")
    assert len(kept) == 3  # the first plus keep_versions
    for version in versions:
        assert version is not None
        assert vault.meta_path(version).exists() == (version in kept)


@pytest.mark.usefixtures("on")
def test_a_session_copy_without_a_bundle_still_works(tmp_path: Path) -> None:
    session = tmp_path / "acc.session"
    _session(session, b"k1")
    version = vault.snapshot_session(session, now=_at(0))
    assert version is not None
    assert not vault.meta_path(version).exists()


def test_a_tombstone_is_encrypted_with_a_key_plain_without_and_purged_with_the_quarantine(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    plain = vault.write_tombstone("acc", b'{"a": 1}', now=_at(0))
    assert plain.name.endswith("-acc.account")
    assert plain.read_bytes() == b'{"a": 1}'
    assert vault.read_bundle(plain) == b'{"a": 1}'

    monkeypatch.setattr(settings.session_vault, "key", vault.new_vault_key())
    sealed = vault.write_tombstone("acc2", _BUNDLE.encode(), now=_at(1))
    assert b"hunter2" not in sealed.read_bytes()
    assert json.loads(vault.read_bundle(sealed))["account_id"] == "a"

    later = (_at(0) + timedelta(days=settings.session_vault.quarantine_days + 1)).timestamp()
    assert vault.purge_quarantine(now=later) == 2
    assert not plain.exists()
    assert not sealed.exists()


@pytest.mark.usefixtures("on")
def test_show_meta_masks_the_passwords_unless_revealed(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    session = tmp_path / "acc.session"
    _session(session, b"k1")
    version = vault.snapshot_session(session, now=_at(0), meta=_BUNDLE.encode())
    assert version is not None

    assert cli.main(["show-meta", str(version)]) == 0
    masked = capsys.readouterr().out
    assert "hunter2" not in masked
    assert '"cloud_password": "***"' in masked
    assert '"pending_cloud_password": null' in masked

    assert cli.main(["show-meta", str(vault.meta_path(version)), "--reveal"]) == 0
    assert "hunter2" in capsys.readouterr().out
    assert cli.main(["show-meta", str(tmp_path / "nope.meta")]) == 1


def test_show_meta_reads_a_plain_tombstone(capsys: pytest.CaptureFixture[str]) -> None:
    tomb = vault.write_tombstone("acc", _BUNDLE.encode(), now=_at(0))
    assert cli.main(["show-meta", str(tomb), "--reveal"]) == 0
    assert "hunter2" in capsys.readouterr().out
