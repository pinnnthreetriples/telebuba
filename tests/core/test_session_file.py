"""``core.session_file``: a read-only verdict on a ``.session`` file, from real SQLite files."""

from __future__ import annotations

import sqlite3
from contextlib import closing
from typing import TYPE_CHECKING

import pytest

from core.session_file import (
    inspect_session_file,
    list_session_stems,
    session_file_of,
)
from tests.session_files import write_pyrogram_session_file, write_session_file

if TYPE_CHECKING:
    from pathlib import Path


def test_a_missing_file_is_missing_and_is_not_created(tmp_path: Path) -> None:
    path = tmp_path / "gone.session"

    assert inspect_session_file(path) == "missing"
    assert list(tmp_path.iterdir()) == []


def test_a_session_with_a_key_is_ok(tmp_path: Path) -> None:
    assert inspect_session_file(write_session_file(tmp_path / "a.session")) == "ok"


def test_a_session_that_never_held_a_key_has_no_key(tmp_path: Path) -> None:
    path = write_session_file(tmp_path / "junk.session", key=None)

    assert inspect_session_file(path) == "no_key"


def test_a_row_with_an_empty_key_has_no_key(tmp_path: Path) -> None:
    assert inspect_session_file(write_session_file(tmp_path / "e.session", key=b"")) == "no_key"


def test_a_file_that_is_not_sqlite_is_not_a_session(tmp_path: Path) -> None:
    path = tmp_path / "text.session"
    path.write_bytes(b"this is certainly not a database" * 20)

    assert inspect_session_file(path) == "not_a_session"


def test_a_truncated_sqlite_file_is_not_a_session(tmp_path: Path) -> None:
    path = write_session_file(tmp_path / "cut.session")
    path.write_bytes(path.read_bytes()[:150])

    assert inspect_session_file(path) == "not_a_session"


def test_a_pyrogram_shaped_file_is_not_a_session(tmp_path: Path) -> None:
    assert inspect_session_file(write_pyrogram_session_file(tmp_path / "p.session")) == (
        "not_a_session"
    )


def test_a_database_without_a_sessions_table_is_not_a_session(tmp_path: Path) -> None:
    path = tmp_path / "other.session"
    with closing(sqlite3.connect(path)) as database:
        database.execute("create table notes (body text)")
        database.commit()

    assert inspect_session_file(path) == "not_a_session"


def test_a_directory_is_not_a_session(tmp_path: Path) -> None:
    folder = tmp_path / "dir.session"
    folder.mkdir()

    assert inspect_session_file(folder) == "not_a_session"


def test_a_file_held_for_writing_is_unreadable_not_judged(tmp_path: Path) -> None:
    """A writer that holds the file proves nothing about the key: the answer is "unreadable"."""
    path = write_session_file(tmp_path / "busy.session")
    holder = sqlite3.connect(path, isolation_level=None)
    try:
        holder.execute("begin exclusive")

        assert inspect_session_file(path) == "unreadable"
    finally:
        holder.execute("rollback")
        holder.close()


def test_the_look_leaves_the_file_unchanged_and_closed(tmp_path: Path) -> None:
    path = write_session_file(tmp_path / "same.session")
    before = path.read_bytes()

    inspect_session_file(path)

    assert path.read_bytes() == before
    path.rename(tmp_path / "moved.session")  # a leftover handle would fail this on Windows


def test_the_file_telethon_stores_is_the_path_plus_the_suffix() -> None:
    assert session_file_of("sessions/acc").as_posix() == "sessions/acc.session"
    assert session_file_of("sessions/acc.session").as_posix() == "sessions/acc.session"


def test_listing_names_session_files_without_scratch_and_side_files(tmp_path: Path) -> None:
    for name in ("one.session", "two.session", "one_rekey.session"):
        (tmp_path / name).write_bytes(b"x")
    (tmp_path / "one.session.rekey-aside").write_bytes(b"x")
    (tmp_path / "one.session-journal").write_bytes(b"x")
    (tmp_path / "notes.txt").write_bytes(b"x")
    (tmp_path / "folder.session").mkdir()

    assert list_session_stems(tmp_path) == ["one", "two"]


def test_listing_a_missing_directory_is_empty(tmp_path: Path) -> None:
    assert list_session_stems(tmp_path / "nowhere") == []


@pytest.mark.parametrize("name", [".session", "x.sess"])
def test_listing_ignores_names_that_are_not_a_session(tmp_path: Path, name: str) -> None:
    (tmp_path / name).write_bytes(b"x")

    assert list_session_stems(tmp_path) == []
