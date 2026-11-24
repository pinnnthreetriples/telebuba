"""Real SQLite ``.session`` files for tests (Telethon's schema), never a fake byte string.

Importing a session validates the upload (``core.session_file``), so a test that needs a
file the importer accepts builds one here. The key is a fixed fake, never a real credential.
"""

from __future__ import annotations

import sqlite3
import tempfile
from contextlib import closing
from pathlib import Path

FAKE_KEY = b"\x07" * 256

_TELETHON_TABLES = (
    "create table version (version integer primary key)",
    "create table sessions (dc_id integer primary key, server_address text, port integer,"
    " auth_key blob, takeout_id integer)",
    "create table entities (id integer primary key, hash integer not null, username text,"
    " phone integer, name text, date integer)",
)


def write_session_file(path: Path, *, key: bytes | None = FAKE_KEY) -> Path:
    """A Telethon-schema session at ``path``; ``key=None`` leaves the ``sessions`` table empty."""
    path.parent.mkdir(parents=True, exist_ok=True)
    with closing(sqlite3.connect(path)) as database:
        for statement in _TELETHON_TABLES:
            database.execute(statement)
        database.execute("insert into version values (7)")
        if key is not None:
            database.execute(
                "insert into sessions values (2, '149.154.167.51', 443, ?, null)", (key,)
            )
        database.commit()
    return path


def write_pyrogram_session_file(path: Path) -> Path:
    """A SQLite file with a ``sessions`` table of Pyrogram's shape: no ``server_address``."""
    path.parent.mkdir(parents=True, exist_ok=True)
    with closing(sqlite3.connect(path)) as database:
        database.execute(
            "create table sessions (dc_id integer primary key, api_id integer, test_mode integer,"
            " auth_key blob, date integer not null, user_id integer, is_bot integer)"
        )
        database.execute("insert into sessions values (2, 1, 0, ?, 0, 1, 0)", (FAKE_KEY,))
        database.commit()
    return path


def session_file_bytes(*, key: bytes | None = FAKE_KEY) -> bytes:
    """The content of a valid Telethon-schema session, for an upload."""
    with tempfile.TemporaryDirectory() as folder:
        return write_session_file(Path(folder) / "upload.session", key=key).read_bytes()
