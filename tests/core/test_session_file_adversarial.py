"""Adversarial probes of ``core.session_file`` and the pool's refusal, on real SQLite files.

Every state a damaged or half-made ``.session`` can be in, a file held by a writer, odd names,
handles left behind, and the second device's own file. Nothing is created or changed at the
path in any of them.
"""

from __future__ import annotations

import sqlite3
import time
from contextlib import closing
from typing import TYPE_CHECKING

import pytest

from core.config import settings
from core.db import _get_engine, configure_database, create_account, insert_device_fingerprint
from core.repositories.device_fingerprint import record_second_device
from core.session_file import inspect_session_file
from core.telegram_client._pool import _reset_for_tests, get_client
from core.telegram_client._pool_errors import SessionFileMissingError
from schemas.accounts import AccountCreate
from schemas.device_fingerprint import DeviceFingerprint
from tests.session_files import write_pyrogram_session_file, write_session_file

if TYPE_CHECKING:
    from collections.abc import Callable, Iterator
    from pathlib import Path

    from schemas.device_fingerprint import DeviceType, TelegramClientProfile


def _empty(path: Path) -> None:
    path.write_bytes(b"")


def _no_tables(path: Path) -> None:
    with closing(sqlite3.connect(path)) as database:
        database.execute("pragma user_version = 1")
        database.commit()
    if not path.exists() or path.stat().st_size == 0:
        # An untouched database stays 0 bytes: give it one real table that is not Telethon's.
        with closing(sqlite3.connect(path)) as database:
            database.execute("create table x (a)")
            database.commit()


def _zero_rows(path: Path) -> None:
    write_session_file(path, key=None)


def _null_key(path: Path) -> None:
    write_session_file(path)
    with closing(sqlite3.connect(path)) as database:
        database.execute("update sessions set auth_key = null")
        database.commit()


def _empty_blob(path: Path) -> None:
    write_session_file(path, key=b"")


def _garbage(path: Path) -> None:
    path.write_bytes(bytes(range(256)) * 40)


def _sqlite_header_only(path: Path) -> None:
    path.write_bytes(b"SQLite format 3\x00" + b"\x00" * 84)


def _pyrogram(path: Path) -> None:
    write_pyrogram_session_file(path)


_BROKEN: dict[str, Callable[[Path], None]] = {
    "empty": _empty,
    "no_tables": _no_tables,
    "zero_rows": _zero_rows,
    "null_key": _null_key,
    "empty_blob": _empty_blob,
    "garbage": _garbage,
    "header_only": _sqlite_header_only,
    "pyrogram": _pyrogram,
}


def _snapshot(folder: Path) -> dict[str, bytes]:
    return {entry.name: entry.read_bytes() for entry in folder.iterdir() if entry.is_file()}


@pytest.mark.parametrize("name", sorted(_BROKEN))
def test_a_broken_file_is_judged_and_left_byte_for_byte(tmp_path: Path, name: str) -> None:
    path = tmp_path / "acc.session"
    _BROKEN[name](path)
    before = _snapshot(tmp_path)

    state = inspect_session_file(path)

    assert state in {"no_key", "not_a_session"}, state
    assert _snapshot(tmp_path) == before  # no journal, no wal, no new table in it
    path.unlink()  # a leftover handle would fail this on Windows


def test_a_file_in_a_write_transaction_is_never_called_missing(tmp_path: Path) -> None:
    path = write_session_file(tmp_path / "busy.session")
    holder = sqlite3.connect(path, isolation_level=None)
    try:
        holder.execute("begin immediate")  # what a connected Telethon client holds
        holder.execute("update sessions set port = 444")

        assert inspect_session_file(path) in {"ok", "unreadable"}

        holder.execute("rollback")
        holder.execute("begin exclusive")
        started = time.monotonic()

        assert inspect_session_file(path) == "unreadable"
        assert time.monotonic() - started < 5  # a short wait, never a hang
    finally:
        holder.close()
    path.rename(tmp_path / "moved.session")


def test_a_wal_database_with_an_open_writer_is_not_called_missing(tmp_path: Path) -> None:
    path = write_session_file(tmp_path / "wal.session")
    holder = sqlite3.connect(path, isolation_level=None)
    try:
        holder.execute("pragma journal_mode = wal")
        holder.execute("begin immediate")
        holder.execute("update sessions set port = 444")

        assert inspect_session_file(path) in {"ok", "unreadable"}
    finally:
        holder.execute("rollback")
        holder.close()


@pytest.mark.parametrize("name", ["with space", "hash#mark", "per%cent", "ünï", "a&b", "x%20y"])
def test_odd_file_names_still_read_as_ok(tmp_path: Path, name: str) -> None:
    path = write_session_file(tmp_path / f"{name}.session")

    assert inspect_session_file(path) == "ok"


def test_a_folder_with_odd_characters_in_the_path_still_reads(tmp_path: Path) -> None:
    folder = tmp_path / "my sessions #1"
    path = write_session_file(folder / "acc.session")

    assert inspect_session_file(path) == "ok"


def test_a_file_with_a_hot_journal_is_left_alone(tmp_path: Path) -> None:
    path = write_session_file(tmp_path / "hot.session")
    journal = tmp_path / "hot.session-journal"
    journal.write_bytes(b"\xd9\xd5\x05\xf9\x20\xa1\x63\xd7" + b"\x00" * 504)
    before = _snapshot(tmp_path)

    state = inspect_session_file(path)

    assert state in {"ok", "unreadable", "not_a_session"}
    assert state != "missing"
    assert _snapshot(tmp_path) == before


# --- the pool refuses, on real files ---------------------------------------------------------


@pytest.fixture(autouse=True)
def _isolate(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    configure_database(tmp_path / "telebuba.db")
    _get_engine()
    monkeypatch.setattr(settings.telegram, "session_dir", tmp_path / "sessions")
    _reset_for_tests()
    yield
    _reset_for_tests()


class _FakeClient:
    def __init__(self, profile: TelegramClientProfile) -> None:
        self.profile = profile
        self.connected = False

    async def connect(self) -> None:
        self.connected = True

    def disconnect(self) -> None:
        self.connected = False

    def is_connected(self) -> bool:
        return self.connected


@pytest.fixture
def built(monkeypatch: pytest.MonkeyPatch) -> list[_FakeClient]:
    clients: list[_FakeClient] = []

    def fake_create(profile: TelegramClientProfile) -> _FakeClient:
        clients.append(_FakeClient(profile))
        return clients[-1]

    monkeypatch.setattr("core.telegram_client._pool.create_telegram_client", fake_create)
    return clients


@pytest.mark.asyncio
@pytest.mark.parametrize("name", sorted(_BROKEN))
async def test_the_pool_refuses_every_broken_file_and_touches_nothing(
    built: list[_FakeClient], name: str
) -> None:
    path = settings.telegram.session_dir / "acc.session"
    path.parent.mkdir(parents=True)
    _BROKEN[name](path)
    before = _snapshot(path.parent)

    with pytest.raises(SessionFileMissingError):
        await get_client("acc")

    assert built == []
    assert _snapshot(path.parent) == before


@pytest.mark.asyncio
async def test_a_busy_file_proceeds_to_the_build(built: list[_FakeClient]) -> None:
    path = write_session_file(settings.telegram.session_dir / "acc.session")
    holder = sqlite3.connect(path, isolation_level=None)
    try:
        holder.execute("begin exclusive")

        await get_client("acc")
    finally:
        holder.execute("rollback")
        holder.close()

    assert len(built) == 1


def _fingerprint(account_id: str, device_type: DeviceType) -> DeviceFingerprint:
    mobile = device_type == "mobile"
    return DeviceFingerprint(
        account_id=account_id,
        device_type=device_type,
        platform="android" if mobile else "windows",
        device_model="GooglePixel 8" if mobile else "Desktop PC",
        system_version="SDK 34" if mobile else "Windows 11",
        app_version="12.10.6 (7112)" if mobile else "5.6.3 x64",
        lang_code="en",
        system_lang_code="en-US",
    )


@pytest.mark.asyncio
async def test_a_missing_second_device_file_is_refused_while_the_primary_is_served(
    built: list[_FakeClient], monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(settings.telegram, "mobile_api_id", 6)
    monkeypatch.setattr(settings.telegram, "mobile_api_hash", "mobile-hash")
    await create_account(
        AccountCreate(account_id="acc", session_name="acc", session_device="desktop"),
    )
    await insert_device_fingerprint(_fingerprint("acc", "desktop"))
    await record_second_device("acc_mobile", _fingerprint("acc", "mobile"))
    folder = settings.telegram.session_dir
    write_session_file(folder / "acc.session")

    await get_client("acc")  # the primary is fine
    with pytest.raises(SessionFileMissingError) as raised:
        await get_client("acc", "mobile")

    assert raised.value.state == "missing"
    assert len(built) == 1
    assert sorted(entry.name for entry in folder.iterdir()) == ["acc.session"]
    # And the primary's client is still the cached one: the refusal evicted nothing.
    assert (await get_client("acc")) is built[0]
