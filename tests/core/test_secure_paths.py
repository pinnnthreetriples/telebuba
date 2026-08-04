"""Owner-only modes for the credential paths — and inertness off POSIX.

The real deploy is Linux, the development checkout is Windows, and ``os.chmod``
means something completely different on each. Both branches are exercised here by
faking ``os.name``, so neither platform's behaviour depends on which one CI happens
to run: the POSIX branch must actually apply the mode, and the other must apply
nothing at all and raise nothing either.
"""

from __future__ import annotations

import asyncio
import os
import stat
from typing import TYPE_CHECKING

import pytest

from core import secure_paths
from core.config import settings
from core.db import configure_database, fetch_account
from core.db_maintenance import run_db_maintenance

if TYPE_CHECKING:
    from pathlib import Path

_POSIX_ONLY = pytest.mark.skipif(os.name != "posix", reason="chmod is a no-op off POSIX")


def _mode(path: Path) -> int:
    return stat.S_IMODE(path.stat().st_mode)


@_POSIX_ONLY
def test_make_private_dir_creates_the_tree_owner_only(tmp_path: Path) -> None:
    target = tmp_path / "sessions"
    secure_paths.make_private_dir(target)
    assert target.is_dir()
    assert _mode(target) == 0o700


@_POSIX_ONLY
def test_make_private_dir_tightens_a_directory_that_already_exists(tmp_path: Path) -> None:
    """``mkdir(mode=...)`` is ignored for an existing dir, so the chmod must be separate."""
    target = tmp_path / "sessions"
    target.mkdir(mode=0o755)
    secure_paths.make_private_dir(target)
    assert _mode(target) == 0o700


@_POSIX_ONLY
def test_make_private_file_restricts_an_existing_file(tmp_path: Path) -> None:
    secret = tmp_path / "acc.session"
    secret.write_bytes(b"credential")
    secret.chmod(0o644)
    secure_paths.make_private_file(secret)
    assert _mode(secret) == 0o600


def test_the_posix_branch_asks_for_0700_and_0600(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Observe the requested modes rather than the result, so this runs everywhere.

    The three assertions above are the ground truth but only execute on Linux; this
    one keeps the POSIX branch covered — and mutation-provable — on a Windows
    checkout, where the modes it asks for would otherwise be unobservable.
    """
    calls: list[int] = []
    monkeypatch.setattr(secure_paths, "_IS_POSIX", True)
    monkeypatch.setattr("pathlib.Path.chmod", lambda _self, mode: calls.append(mode))
    target = tmp_path / "sessions"
    secure_paths.make_private_dir(target)
    (target / "acc.session").write_bytes(b"credential")
    secure_paths.make_private_file(target / "acc.session")
    assert calls == [0o700, 0o600]


def test_off_posix_the_directory_is_created_but_never_chmodded(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """On Windows ``chmod`` only toggles the read-only bit, so it must not be called."""
    calls: list[int] = []
    monkeypatch.setattr(secure_paths, "_IS_POSIX", False)
    monkeypatch.setattr("pathlib.Path.chmod", lambda _self, mode: calls.append(mode))
    target = tmp_path / "sessions"
    secure_paths.make_private_dir(target)
    secure_paths.make_private_file(target)
    assert target.is_dir()
    assert calls == []


def _recorded_modes(monkeypatch: pytest.MonkeyPatch) -> dict[str, int]:
    """Capture the modes requested, so both call sites are provable off POSIX."""
    modes: dict[str, int] = {}
    monkeypatch.setattr(secure_paths, "_IS_POSIX", True)
    monkeypatch.setattr(
        "pathlib.Path.chmod",
        lambda self, mode: modes.__setitem__(self.name, mode),
    )
    return modes


def test_the_database_and_its_directory_are_owner_only(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """telebuba.db is a credential store, not just application state.

    The proxies table holds PLAINTEXT proxy passwords (read back verbatim by
    ``core.repositories.proxies``) next to every ``password_hash``, and SQLite
    creates the file at the default umask — so it sat world-readable beside the
    ``sessions/`` dir this slice hardened to 0700.
    """
    modes = _recorded_modes(monkeypatch)
    db_dir = tmp_path / "data"
    configure_database(db_dir / "telebuba.db")
    asyncio.run(fetch_account("nobody"))  # forces _build_engine

    assert modes["data"] == 0o700
    assert modes["telebuba.db"] == 0o600


def test_the_backup_and_its_directory_are_owner_only(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A backup is a byte-for-byte copy of that same credential store."""
    configure_database(tmp_path / "telebuba.db")
    monkeypatch.setattr(settings.db, "backup_enabled", True)
    monkeypatch.setattr(settings.db, "backup_dir", tmp_path / "backups")
    modes = _recorded_modes(monkeypatch)

    target = run_db_maintenance()

    assert target is not None
    assert modes["backups"] == 0o700
    assert modes[target.name] == 0o600


def test_a_filesystem_that_refuses_chmod_does_not_break_the_write(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A mounted share with no POSIX modes must not fail an otherwise valid import."""

    def _refuse(_self: Path, _mode: int) -> None:
        raise PermissionError(1, "operation not permitted")

    monkeypatch.setattr(secure_paths, "_IS_POSIX", True)
    monkeypatch.setattr("pathlib.Path.chmod", _refuse)
    target = tmp_path / "sessions"
    secure_paths.make_private_dir(target)
    secure_paths.make_private_file(target)
    assert target.is_dir()
