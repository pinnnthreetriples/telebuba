"""The re-key's session files: copying one into another in place, and reading which key each holds.

Split out of ``_rekey`` (file size). Everything here touches only files, never Telegram.
"""

from __future__ import annotations

import asyncio
import hashlib
import logging
import sqlite3
import time
from contextlib import closing
from pathlib import Path

from anyio import Path as AsyncPath

# Stdlib sink, like ``_rekey``.
logger = logging.getLogger(__name__)

# The suffix of the file the imported session is set aside as while the swap is verified.
ASIDE_SUFFIX = ".rekey-aside"
# How long a write to the session file waits for another connection that holds it.
LOCK_WAIT_SECONDS = 10.0


def file_key(session_file: str) -> bytes | None:
    """The auth key a Telethon session file holds, read without changing it; ``None`` if unknown."""
    path = Path(session_file)
    if not path.exists():
        return None
    try:
        with closing(sqlite3.connect(f"{path.resolve().as_uri()}?mode=ro", uri=True)) as db:
            row = db.execute("select auth_key from sessions").fetchone()
    except sqlite3.Error:
        return None
    return bytes(row[0]) if row and row[0] else None


def key_mark(session_file: str) -> str | None:
    """A one-way mark (sha256 hex) of the key a session file holds; ``None`` if unknown.

    What the intent journal keeps to tell, after a crash, whether the account's file still
    holds the key a re-key began with. The key itself is never stored.
    """
    key = file_key(session_file)
    return None if key is None else hashlib.sha256(key).hexdigest()


async def same_key(first: str, second: str) -> bool:
    """Whether two session files provably hold the same auth key."""
    key = await asyncio.to_thread(file_key, first)
    return key is not None and key == await asyncio.to_thread(file_key, second)


async def unlink(path: str) -> None:
    await AsyncPath(path).unlink()


async def tidy(path: str) -> None:
    """Remove a side file the swap no longer needs; a failure is only logged."""
    try:
        await unlink(path)
    except FileNotFoundError:
        return
    except OSError as exc:
        logger.warning("re-key side file kept (%s)", type(exc).__name__)


def copy_session(source: str, target: str) -> None:
    """Make the SQLite file ``target`` hold exactly what ``source`` holds, in place.

    SQLite's backup writes the target's pages under its own lock: a client that still has the
    target open does not stop it. A rename does, on Windows: SQLite opens its files without
    delete sharing, so ONE leftover connection fails it (``WinError 32``). Telethon's session
    reopens its file on any call, so a background task that kept the evicted client leaves
    exactly that behind, and nothing here can reach it.

    A connection that holds the target for writing would make ``backup`` wait for ever (it
    retries a busy target on its own), so the wait is bounded: ``sqlite3.OperationalError``.
    """
    deadline = time.monotonic() + LOCK_WAIT_SECONDS

    def give_up(status: int, _remaining: int, _total: int) -> None:
        if status in {sqlite3.SQLITE_BUSY, sqlite3.SQLITE_LOCKED} and time.monotonic() > deadline:
            msg = "the session file stayed locked"
            raise sqlite3.OperationalError(msg)

    reader = sqlite3.connect(source)
    try:
        # No busy handler of its own: the loop below is the one wait, and it is bounded.
        writer = sqlite3.connect(target, timeout=0)
        try:
            reader.backup(writer, sleep=0.05, progress=give_up)
        finally:
            writer.close()
    finally:
        reader.close()
