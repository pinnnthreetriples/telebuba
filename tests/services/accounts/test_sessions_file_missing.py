"""A session file that is gone is "restore it", not "Telegram revoked the key".

A moved or quarantined ``.session`` used to be replaced by an empty one on the next check and
journalled as a dead session. The check now reads ``session_file_missing``: not a death, no
"session dead" notification, no empty file made, and a login code or passkey still allowed.
Real SQLite session files and the real pool; only Telethon's constructor is a tripwire.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, cast

import pytest

from core.config import settings
from core.db import fetch_account, update_account_from_session_check
from core.repositories.lifecycle import list_lifecycle_events
from schemas.accounts import AccountCheckRequest, AccountCreate, health_for_status
from schemas.telegram_session import TelegramSessionCheckResult
from services.accounts import add_account, check_account_session
from services.accounts.login import KEY_GONE_STATUSES
from services.inbox_runtime import _NON_AUTHORIZED_STATUSES
from services.lifecycle_journal import DEAD_SESSION_STATUSES
from tests.session_files import write_session_file

if TYPE_CHECKING:
    from pathlib import Path

    from schemas.accounts import AccountStatus


@pytest.fixture(autouse=True)
def _real_check(monkeypatch: pytest.MonkeyPatch) -> list[str]:
    """The check runs for real; building a Telethon client at all is the failure."""
    monkeypatch.setattr(settings.telegram, "api_id", 1)
    monkeypatch.setattr(settings.telegram, "api_hash", "hash")
    built: list[str] = []

    def tripwire(profile: object) -> None:
        built.append(str(profile))
        pytest.fail("a Telethon client was built over a missing session file")

    monkeypatch.setattr("core.telegram_client._pool.create_telegram_client", tripwire)
    return built


@pytest.fixture
def events(monkeypatch: pytest.MonkeyPatch) -> list[tuple[str, str | None, dict[str, object]]]:
    seen: list[tuple[str, str | None, dict[str, object]]] = []

    async def record(
        _level: str,
        event: str,
        *,
        account_id: str | None = None,
        extra: dict[str, object] | None = None,
    ) -> None:
        seen.append((event, account_id, extra or {}))

    monkeypatch.setattr("services.accounts.sessions.log_event", record)
    monkeypatch.setattr("services.lifecycle_journal.log_event", record)
    return seen


async def _account_with_status(account_id: str, status: AccountStatus) -> None:
    await add_account(AccountCreate(account_id=account_id, session_name=account_id))
    if status != "new":
        await update_account_from_session_check(
            TelegramSessionCheckResult(
                account_id=account_id,
                session_path=account_id,
                status=status,
                is_temporary=False,
            ),
        )


def _sessions() -> list[Path]:
    folder = settings.telegram.session_dir
    return sorted(folder.glob("*")) if folder.exists() else []


@pytest.mark.asyncio
@pytest.mark.parametrize("stored", ["alive", "frozen", "network_error", "session_file_missing"])
async def test_a_missing_file_is_session_file_missing_and_not_created(
    stored: str, events: list[tuple[str, str | None, dict[str, object]]]
) -> None:
    await _account_with_status("lost", cast("AccountStatus", stored))

    saved = await check_account_session(AccountCheckRequest(account_id="lost"))

    assert saved.status == "session_file_missing"
    assert _sessions() == []
    assert ("account_session_file_missing", "lost", {}) in events


@pytest.mark.asyncio
async def test_a_file_without_a_key_reads_the_same(
    events: list[tuple[str, str | None, dict[str, object]]],
) -> None:
    await _account_with_status("junk", "alive")
    junk = write_session_file(settings.telegram.session_dir / "junk.session", key=None)
    before = junk.read_bytes()

    saved = await check_account_session(AccountCheckRequest(account_id="junk"))

    assert saved.status == "session_file_missing"
    assert junk.read_bytes() == before
    assert ("account_session_file_missing", "junk", {}) in events


@pytest.mark.asyncio
async def test_it_is_not_journalled_as_a_session_death(
    events: list[tuple[str, str | None, dict[str, object]]],
) -> None:
    await _account_with_status("lost", "alive")

    await check_account_session(AccountCheckRequest(account_id="lost"))

    assert await list_lifecycle_events("lost") == []
    assert "account_session_dead" not in [event for event, _account, _extra in events]
    assert "session_file_missing" not in DEAD_SESSION_STATUSES


@pytest.mark.asyncio
@pytest.mark.parametrize("stored", ["new", "unauthorized"])
async def test_an_account_that_expects_no_file_keeps_the_old_verdict(
    stored: str, events: list[tuple[str, str | None, dict[str, object]]]
) -> None:
    """A placeholder never signed in, or a session the operator wiped: no file is expected."""
    await _account_with_status("fresh", cast("AccountStatus", stored))

    saved = await check_account_session(AccountCheckRequest(account_id="fresh"))

    assert saved.status == "unauthorized"
    assert _sessions() == []
    assert "account_session_file_missing" not in [event for event, _account, _extra in events]


@pytest.mark.asyncio
async def test_the_account_is_not_usable_but_may_log_in_again() -> None:
    assert health_for_status("session_file_missing") == "fail"
    assert "session_file_missing" in _NON_AUTHORIZED_STATUSES
    assert "session_file_missing" in KEY_GONE_STATUSES  # a login code makes a new key


@pytest.mark.asyncio
async def test_the_inbox_is_stopped_not_started(monkeypatch: pytest.MonkeyPatch) -> None:
    await _account_with_status("lost", "alive")
    calls: list[str] = []

    async def start(account_id: str) -> None:
        calls.append(f"start {account_id}")

    async def stop(account_id: str, **_kwargs: object) -> None:
        calls.append(f"stop {account_id}")

    monkeypatch.setattr("services.accounts.sessions.start_account_inbox", start)
    monkeypatch.setattr("services.accounts.sessions.stop_account_inbox", stop)

    await check_account_session(AccountCheckRequest(account_id="lost"))

    assert calls == ["stop lost"]
    assert (await fetch_account("lost")).status == "session_file_missing"  # ty: ignore[unresolved-attribute]
