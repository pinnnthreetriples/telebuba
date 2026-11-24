"""Adversarial probes of the ``session_file_missing`` verdict and what runs around it."""

from __future__ import annotations

import asyncio
from typing import TYPE_CHECKING, cast

import pytest
import pytest_asyncio

from core.config import settings
from core.db import update_account_from_session_check
from core.repositories.lifecycle import list_lifecycle_events
from schemas.accounts import AccountCheckRequest, AccountCreate, AccountSessionFileImport
from schemas.telegram_session import TelegramSessionCheckResult
from services import inbox_runtime
from services.accounts import add_account, check_account_session, import_account_session
from tests.session_files import session_file_bytes

if TYPE_CHECKING:
    from collections.abc import AsyncIterator
    from pathlib import Path

    from schemas.telegram_session import SessionCheckStatus

Event = tuple[str, str | None]


@pytest.fixture(autouse=True)
def _real_check(monkeypatch: pytest.MonkeyPatch) -> list[str]:
    monkeypatch.setattr(settings.telegram, "api_id", 1)
    monkeypatch.setattr(settings.telegram, "api_hash", "hash")
    monkeypatch.setattr(settings.telegram, "allow_direct_connection", True)
    built: list[str] = []

    def tripwire(profile: object) -> None:
        built.append(str(profile))
        pytest.fail("a Telethon client was built over a missing session file")

    monkeypatch.setattr("core.telegram_client._pool.create_telegram_client", tripwire)
    return built


@pytest.fixture
def events(monkeypatch: pytest.MonkeyPatch) -> list[Event]:
    seen: list[Event] = []

    async def record(
        _level: str, event: str, *, account_id: str | None = None, **_kw: object
    ) -> None:
        seen.append((event, account_id))

    monkeypatch.setattr("services.accounts.sessions.log_event", record)
    monkeypatch.setattr("services.lifecycle_journal.log_event", record)
    return seen


async def _set_status(account_id: str, status: str) -> None:
    await update_account_from_session_check(
        TelegramSessionCheckResult(
            account_id=account_id,
            session_path=account_id,
            status=cast("SessionCheckStatus", status),
            is_temporary=False,
        ),
    )


def _folder() -> Path:
    return settings.telegram.session_dir


@pytest.mark.asyncio
@pytest.mark.parametrize("content", [b"", b"not sqlite at all" * 20], ids=["empty", "garbage"])
async def test_an_alive_account_with_a_broken_file_is_session_file_missing_and_untouched(
    content: bytes, events: list[Event]
) -> None:
    await add_account(AccountCreate(account_id="acc", session_name="acc"))
    await _set_status("acc", "alive")
    _folder().mkdir(parents=True, exist_ok=True)
    path = _folder() / "acc.session"
    path.write_bytes(content)

    saved = await check_account_session(AccountCheckRequest(account_id="acc"))

    assert saved.status == "session_file_missing"
    assert path.read_bytes() == content
    assert sorted(p.name for p in _folder().iterdir()) == ["acc.session"]
    assert await list_lifecycle_events("acc") == []
    assert ("account_session_file_missing", "acc") in events


@pytest.mark.asyncio
async def test_an_imported_account_whose_file_vanishes_before_its_first_check_reads_unauthorized(
    events: list[Event],
) -> None:
    """Documents the trade-off: ``new`` also covers an import whose key was validated."""
    account = await import_account_session(
        AccountSessionFileImport(filename="seller.session", content=session_file_bytes()),
    )
    assert account.status == "new"
    (_folder() / "seller.session").unlink()

    saved = await check_account_session(AccountCheckRequest(account_id="seller"))

    assert saved.status == "unauthorized"
    assert "account_session_file_missing" not in [event for event, _a in events]


@pytest.mark.asyncio
async def test_a_failed_check_never_creates_anything_even_when_repeated(
    events: list[Event],
) -> None:
    await add_account(AccountCreate(account_id="acc", session_name="acc"))
    await _set_status("acc", "alive")

    for _ in range(3):
        saved = await check_account_session(AccountCheckRequest(account_id="acc"))
        assert saved.status == "session_file_missing"

    assert not _folder().exists() or list(_folder().iterdir()) == []
    assert [e for e, _a in events].count("account_session_file_missing") == 3  # logged per check


@pytest_asyncio.fixture
async def _inbox() -> AsyncIterator[None]:
    inbox_runtime._reset_for_tests()
    inbox_runtime._RUNNING = True
    yield
    for task in [*inbox_runtime._RETRIES.values(), *inbox_runtime._MONITORS.values()]:
        task.cancel()
    await asyncio.gather(
        *inbox_runtime._RETRIES.values(), *inbox_runtime._MONITORS.values(), return_exceptions=True
    )
    inbox_runtime._reset_for_tests()


@pytest.mark.asyncio
@pytest.mark.usefixtures("_inbox")
async def test_a_vanished_file_does_not_start_an_endless_inbox_retry() -> None:
    """A missing file is permanent: retrying every <= 60 s forever only fills the log."""
    await add_account(AccountCreate(account_id="acc", session_name="acc"))
    await _set_status("acc", "alive")

    await inbox_runtime.start_account_inbox("acc")

    assert inbox_runtime._RETRIES == {}
