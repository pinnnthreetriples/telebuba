"""A death found later is dated by its earliest recorded evidence, never invented.

The journal row's ``created_at`` is when the death was found; ``extra.evidence_at`` is
the earliest logged Telegram refusal that showed it, but never one at or before the
last thing that proved the account alive (a successful action, an @SpamBot answer, the
life's start). Without such a refusal there is no ``evidence_at``: the finding dates it.
"""

from __future__ import annotations

import logging

import pytest

from core.db import create_account, fetch_account, update_account_from_session_check
from core.repositories.lifecycle import append_lifecycle_event, list_lifecycle_events
from core.repositories.logs import insert_log_row
from schemas.accounts import AccountCreate
from schemas.logs import LogEventInput
from schemas.telegram_session import TelegramSessionCheckResult
from services import lifecycle_journal
from services.lifecycle_journal import (
    prepare_account_deleted,
    record_account_deleted,
    record_session_death,
)

_FROZEN_READ = "warming_telegram_read_channel_failed"


async def _alive() -> None:
    await create_account(AccountCreate(account_id="a1"))
    await update_account_from_session_check(
        TelegramSessionCheckResult(
            account_id="a1", session_path="a1", status="alive", is_temporary=False
        ),
    )


async def _log(event: str, *, error_type: str | None = None, account_id: str = "a1") -> str:
    level = "ERROR" if error_type else "INFO"
    extra: dict[str, object] = {"error_type": error_type} if error_type else {}
    row = await insert_log_row(
        LogEventInput(level=level, event=event, account_id=account_id, extra=extra)
    )
    return row.created_at


async def _die(status: str = "frozen") -> dict[str, object]:
    account = await fetch_account("a1")
    assert account is not None
    await record_session_death(account, status)  # ty: ignore[invalid-argument-type]
    [death] = [e for e in await list_lifecycle_events("a1") if e.kind == "session_dead"]
    return death.extra


@pytest.mark.asyncio
async def test_the_first_refusal_after_the_last_success_dates_the_death() -> None:
    await _alive()
    await _log(_FROZEN_READ, error_type="account_frozen")  # before a later success: not it
    await _log("warming_telegram_join_channel")
    first = await _log(_FROZEN_READ, error_type="account_frozen")
    await _log("warming_telegram_react_failed", error_type="account_frozen")

    extra = await _die()

    assert extra == {"status": "frozen", "evidence_at": first, "evidence_event": _FROZEN_READ}


@pytest.mark.asyncio
async def test_without_a_refusal_the_finding_dates_the_death() -> None:
    await _alive()
    await _log(_FROZEN_READ, error_type="account_frozen")
    await _log("telegram_join_channel")  # the account worked after that refusal

    assert await _die() == {"status": "frozen"}


@pytest.mark.asyncio
async def test_an_spambot_answer_proves_the_account_alive_after_a_refusal() -> None:
    await _alive()
    await _log(_FROZEN_READ, error_type="account_frozen")
    await append_lifecycle_event("a1", "spambot_verdict", {"status": "clean"})

    assert await _die() == {"status": "frozen"}


@pytest.mark.asyncio
async def test_only_refusals_that_show_this_death_count() -> None:
    await _alive()
    await _log(_FROZEN_READ, error_type="ChannelPrivateError")
    await _log(_FROZEN_READ, error_type="session_dead", account_id="other")
    frozen_shown = await _log(_FROZEN_READ, error_type="account_frozen")
    revoked_shown = await _log("telegram_send_message_failed", error_type="session_dead")

    assert (await _die("frozen"))["evidence_at"] == frozen_shown
    revoked = await lifecycle_journal.death_evidence(
        "a1", ("session_dead", "account_deactivated"), None
    )
    assert revoked == {
        "evidence_at": revoked_shown,
        "evidence_event": "telegram_send_message_failed",
    }


@pytest.mark.asyncio
async def test_a_failed_evidence_read_still_journals_the_death(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    await _alive()

    async def explode(*_args: object) -> str | None:
        msg = "database is locked"
        raise RuntimeError(msg)

    monkeypatch.setattr(lifecycle_journal, "latest_telegram_success_at", explode)

    with caplog.at_level(logging.WARNING, logger="services.lifecycle_journal"):
        extra = await _die("unauthorized")

    assert extra == {"status": "unauthorized"}
    assert "death evidence read failed" in caplog.text


@pytest.mark.asyncio
async def test_a_deletion_as_dead_carries_its_evidence() -> None:
    await _alive()
    await _log("telegram_join_channel")
    shown = await _log("telegram_join_channel_failed", error_type="account_deactivated")

    await record_account_deleted(await prepare_account_deleted("a1", "dead"))

    [deleted] = await list_lifecycle_events("a1")
    assert deleted.extra == {
        "reason": "dead",
        "reason_source": "operator",
        "evidence_at": shown,
        "evidence_event": "telegram_join_channel_failed",
    }


@pytest.mark.asyncio
async def test_a_retirement_carries_no_evidence() -> None:
    await _alive()
    await _log("telegram_join_channel_failed", error_type="account_deactivated")

    await record_account_deleted(await prepare_account_deleted("a1", "retired"))

    [deleted] = await list_lifecycle_events("a1")
    assert deleted.extra == {"reason": "retired", "reason_source": "operator"}
