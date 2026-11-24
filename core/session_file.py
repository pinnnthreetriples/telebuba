"""A read-only look at a Telethon ``.session`` file: is it there, and does it hold a key?

A ``.session`` is the ONLY copy of an account's auth key. Telethon opens whatever sits at the
path and, when nothing does, creates an empty database and later writes a fresh, unregistered
key into it: a file that was moved away (antivirus, a restore gone wrong) then reads as a key
Telegram revoked, and the junk file blocks putting the real one back. So callers ask first.

The look never creates, writes or locks anything: SQLite's ``mode=ro``, a short wait, and the
connection is always closed (on Windows one leftover handle fails a later rename or move).
A verdict is only as good as the read: ``unreadable`` proves nothing and callers carry on.
"""

from __future__ import annotations

import contextlib
import sqlite3
from pathlib import Path
from typing import Literal

# ``missing``: no file. ``no_key``: a Telethon session with no row holding an auth key (what a
# client leaves when it never connected). ``ok``: a key is there. ``not_a_session``: not
# SQLite, or no Telethon ``sessions`` table (a Pyrogram file has no ``server_address``).
# ``unreadable``: could not be read right now (locked, no permission).
SessionFileState = Literal["missing", "no_key", "ok", "not_a_session", "unreadable"]

SESSION_SUFFIX = ".session"
# The suffix of the scratch session a re-key builds beside the account's own file
# (``services.accounts.session_rekey``): it belongs to no account row and is never an orphan.
REKEY_SCRATCH_SUFFIX = "_rekey"
# How long a read waits for a connection that holds the file for writing.
_READ_WAIT_SECONDS = 1.0
_REQUIRED_COLUMNS = frozenset({"server_address", "auth_key"})


def session_file_of(session_path: str) -> Path:
    """The file Telethon stores a session in: ``<path>.session``, unless the path says so."""
    return Path(
        session_path if session_path.endswith(SESSION_SUFFIX) else session_path + SESSION_SUFFIX
    )


def _state_of(database: sqlite3.Connection) -> SessionFileState:
    columns = {row[1] for row in database.execute("pragma table_info(sessions)")}
    if not columns >= _REQUIRED_COLUMNS:
        return "not_a_session"
    row = database.execute("select 1 from sessions where length(auth_key) > 0 limit 1").fetchone()
    return "ok" if row else "no_key"


def inspect_session_file(path: Path) -> SessionFileState:
    """What ``path`` holds, read without creating or changing anything. Never raises."""
    try:
        if not path.exists():
            return "missing"
        if not path.is_file():
            return "not_a_session"
        uri = f"{path.resolve().as_uri()}?mode=ro"
        with contextlib.closing(
            sqlite3.connect(uri, uri=True, timeout=_READ_WAIT_SECONDS)
        ) as database:
            return _state_of(database)
    except sqlite3.OperationalError:
        # Locked, unreadable, or gone between the check and the open.
        return "unreadable" if _exists(path) else "missing"
    except sqlite3.DatabaseError:
        # Not a database, or damaged beyond reading as one.
        return "not_a_session"
    except OSError:
        return "unreadable"


def _exists(path: Path) -> bool:
    try:
        return path.exists()
    except OSError:
        return True


def list_session_stems(directory: Path) -> list[str]:
    """The names (without ``.session``) of the session files directly in ``directory``.

    Re-key scratch files are left out; a missing or unreadable directory is an empty list.
    """
    try:
        return sorted(
            entry.name.removesuffix(SESSION_SUFFIX)
            for entry in directory.iterdir()
            if entry.name.endswith(SESSION_SUFFIX)
            and entry.name != SESSION_SUFFIX
            and not entry.name.removesuffix(SESSION_SUFFIX).endswith(REKEY_SCRATCH_SUFFIX)
            and entry.is_file()
        )
    except OSError:
        return []
