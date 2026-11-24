"""Adversarial probes of the guard's start-up file look: real files, many accounts, odd states."""

from __future__ import annotations

import sqlite3
import time
from typing import TYPE_CHECKING, cast

import pytest

from core.config import settings
from core.db import insert_device_fingerprint, update_account_from_session_check
from core.repositories.device_fingerprint import record_second_device
from schemas.accounts import AccountCreate
from schemas.device_fingerprint import DeviceFingerprint
from schemas.telegram_session import TelegramSessionCheckResult
from services.accounts import add_account
from services.session_guard import _file_check, _loop
from tests.session_files import write_session_file

if TYPE_CHECKING:
    from collections.abc import Iterator

    from schemas.device_fingerprint import DeviceType

Event = tuple[str, str, str | None, dict[str, object]]


@pytest.fixture(autouse=True)
def _fresh() -> Iterator[None]:
    _loop.reset_for_tests()
    yield
    _loop.reset_for_tests()


@pytest.fixture
def events(monkeypatch: pytest.MonkeyPatch) -> list[Event]:
    seen: list[Event] = []

    async def record(
        level: str, event: str, *, account_id: str | None = None, extra: dict | None = None
    ) -> None:
        seen.append((level, event, account_id, extra or {}))

    monkeypatch.setattr(_file_check, "log_event", record)
    return seen


async def _account(account_id: str, status: str = "alive") -> None:
    await add_account(AccountCreate(account_id=account_id, session_name=account_id))
    if status != "new":
        await update_account_from_session_check(
            TelegramSessionCheckResult(
                account_id=account_id,
                session_path=account_id,
                status=cast("str", status),  # ty: ignore[invalid-argument-type]
                is_temporary=False,
            )
        )


def _told(events: list[Event]) -> list[str]:
    return sorted(
        str(account) for _l, event, account, _x in events if event == "account_session_file_missing"
    )


@pytest.mark.asyncio
async def test_forty_accounts_are_looked_at_quickly_and_logged_once_each(
    events: list[Event],
) -> None:
    folder = settings.telegram.session_dir
    for index in range(40):
        name = f"acc{index:02d}"
        await _account(name)
        if index % 4:
            write_session_file(folder / f"{name}.session")
    started = time.monotonic()

    await _file_check.run_file_reconciliation()

    assert time.monotonic() - started < 10
    assert _told(events) == sorted(f"acc{index:02d}" for index in range(0, 40, 4))
    assert [e for _l, e, _a, _x in events if e == "session_orphan_files_found"] == []


@pytest.mark.asyncio
async def test_a_second_device_file_that_is_gone_is_told_once_for_the_account(
    events: list[Event], monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(settings.telegram, "mobile_api_id", 6)
    monkeypatch.setattr(settings.telegram, "mobile_api_hash", "mobile-hash")
    await _account("acc")
    device: DeviceType = "mobile"
    await insert_device_fingerprint(
        DeviceFingerprint(
            account_id="acc",
            device_type="desktop",
            platform="windows",
            device_model="Desktop PC",
            system_version="Windows 11",
            app_version="5.6.3 x64",
            lang_code="en",
            system_lang_code="en-US",
        )
    )
    await record_second_device(
        "acc_mobile",
        DeviceFingerprint(
            account_id="acc",
            device_type=device,
            platform="android",
            device_model="GooglePixel 8",
            system_version="SDK 34",
            app_version="12.10.6 (7112)",
            lang_code="en",
            system_lang_code="en-US",
        ),
    )
    write_session_file(settings.telegram.session_dir / "acc.session")  # the primary is fine

    await _file_check.run_file_reconciliation()

    assert _told(events) == ["acc"]


@pytest.mark.asyncio
async def test_scratch_import_temporary_and_journal_files_are_nobodys_orphans(
    events: list[Event],
) -> None:
    folder = settings.telegram.session_dir
    await _account("acc")
    write_session_file(folder / "acc.session")
    (folder / "acc.session.importing").write_bytes(b"half")
    (folder / "other.session.importing").write_bytes(b"half")
    (folder / "acc.session-journal").write_bytes(b"x")
    (folder / "acc.session.rekey-aside").write_bytes(b"x")
    write_session_file(folder / "acc_rekey.session")
    before = {path.name: path.read_bytes() for path in folder.iterdir()}

    await _file_check.run_file_reconciliation()

    assert [e for _l, e, _a, _x in events] == []
    assert {path.name: path.read_bytes() for path in folder.iterdir()} == before


@pytest.mark.asyncio
async def test_a_file_held_for_writing_is_not_told_as_missing(events: list[Event]) -> None:
    folder = settings.telegram.session_dir
    await _account("busy")
    path = write_session_file(folder / "busy.session")
    holder = sqlite3.connect(path, isolation_level=None)
    try:
        holder.execute("begin exclusive")

        await _file_check.run_file_reconciliation()
    finally:
        holder.execute("rollback")
        holder.close()

    assert _told(events) == []


@pytest.mark.asyncio
@pytest.mark.parametrize("content", [b"", b"garbage" * 50], ids=["empty", "garbage"])
async def test_an_alive_account_whose_file_is_not_a_session_is_told_like_a_missing_one(
    events: list[Event], content: bytes
) -> None:
    """The pool refuses this file and the session check calls it ``session_file_missing``."""
    folder = settings.telegram.session_dir
    await _account("broken")
    folder.mkdir(parents=True, exist_ok=True)
    (folder / "broken.session").write_bytes(content)

    await _file_check.run_file_reconciliation()

    assert _told(events) == ["broken"]
