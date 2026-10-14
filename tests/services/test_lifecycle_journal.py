"""The lifecycle journal service: never-raising writes and the delete-reason guess."""

from __future__ import annotations

import logging
from datetime import UTC, datetime
from typing import TYPE_CHECKING

import pytest

from core.db import (
    create_account,
    delete_account,
    update_account_from_session_check,
    upsert_spam_status,
)
from core.repositories.lifecycle import append_lifecycle_event, list_lifecycle_events
from schemas.accounts import AccountCreate
from schemas.spam_status import SpamStatusVerdict
from schemas.telegram_session import TelegramSessionCheckResult
from services import lifecycle_journal
from services.lifecycle_journal import (
    guess_delete_reason,
    prepare_account_deleted,
    record_account_deleted,
    record_lifecycle_event,
)

if TYPE_CHECKING:
    from schemas.lifecycle import LifecycleEventKind
    from schemas.telegram_session import SessionCheckStatus


def _explode(*_args: object, **_kwargs: object) -> None:
    msg = "database is locked"
    raise RuntimeError(msg)


async def _async_explode(*args: object, **kwargs: object) -> None:
    _explode(*args, **kwargs)


async def _account(account_id: str = "a1", status: SessionCheckStatus = "alive") -> None:
    await create_account(AccountCreate(account_id=account_id))
    await update_account_from_session_check(
        TelegramSessionCheckResult(
            account_id=account_id,
            session_path=account_id,
            status=status,
            is_temporary=False,
            user_id=4242,
        ),
    )


@pytest.mark.asyncio
async def test_record_writes_a_row_with_the_user_id_snapshot() -> None:
    await _account()

    await record_lifecycle_event("a1", "promoted")

    [event] = await list_lifecycle_events("a1")
    assert (event.kind, event.tg_user_id) == ("promoted", 4242)


@pytest.mark.asyncio
async def test_record_swallows_and_logs_a_write_failure(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    monkeypatch.setattr(lifecycle_journal, "append_lifecycle_event", _async_explode)

    with caplog.at_level(logging.WARNING, logger="services.lifecycle_journal"):
        await record_lifecycle_event("a1", "warmed")

    assert "lifecycle journal write failed" in caplog.text
    assert "RuntimeError" in caplog.text


@pytest.mark.asyncio
async def test_a_healthy_account_is_guessed_retired() -> None:
    await _account()
    assert await guess_delete_reason("a1") == "retired"
    assert await guess_delete_reason("never-existed") == "retired"


@pytest.mark.asyncio
@pytest.mark.parametrize("status", ["unauthorized", "account_error", "frozen"])
async def test_a_dead_session_status_is_guessed_dead(status: SessionCheckStatus) -> None:
    await _account(status=status)
    assert await guess_delete_reason("a1") == "dead"


@pytest.mark.asyncio
async def test_a_spam_blocked_account_is_guessed_written_off() -> None:
    """A spam block leaves a working account: deleting it is the operator writing it off."""
    await _account()
    await upsert_spam_status(
        SpamStatusVerdict(
            account_id="a1", status="limited", checked_at=datetime.now(UTC).isoformat()
        )
    )
    assert await guess_delete_reason("a1") == "retired"


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("kind", "extra", "expected"),
    [
        ("session_dead", {"status": "unauthorized"}, "dead"),
        ("spambot_verdict", {"status": "limited"}, "retired"),
        ("spambot_verdict", {"status": "clean"}, "retired"),
        ("quarantined", {"status": "peer_flood"}, "retired"),
    ],
)
async def test_the_journal_remembers_a_past_death(
    kind: LifecycleEventKind, extra: dict[str, object], expected: str
) -> None:
    """The live verdict may have recovered since; the journal still knows it was limited."""
    await _account()
    await append_lifecycle_event("a1", kind, extra)
    assert await guess_delete_reason("a1") == expected


@pytest.mark.asyncio
async def test_a_reimported_id_does_not_inherit_a_previous_death() -> None:
    await append_lifecycle_event("a1", "spambot_verdict", {"status": "limited"})
    await append_lifecycle_event("a1", "deleted", {"reason": "dead"})
    await _account()
    assert await guess_delete_reason("a1") == "retired"


@pytest.mark.asyncio
async def test_a_past_death_is_ignored_even_when_its_deleted_row_was_never_written() -> None:
    """The ``deleted`` row is best-effort; the live row's creation time is the other fence."""
    await append_lifecycle_event("a1", "session_dead", {"status": "unauthorized"})
    await _account()
    assert await guess_delete_reason("a1") == "retired"


@pytest.mark.asyncio
async def test_a_prepared_deletion_writes_nothing_until_recorded() -> None:
    await _account(status="unauthorized")

    pending = await prepare_account_deleted("a1", "retired")

    assert await list_lifecycle_events("a1") == []
    await record_account_deleted(pending)
    [event] = await list_lifecycle_events("a1")
    assert event.kind == "deleted"
    assert event.extra == {"reason": "retired", "reason_source": "operator"}


@pytest.mark.asyncio
async def test_a_prepared_deletion_falls_back_to_the_guess() -> None:
    await _account(status="unauthorized")

    await record_account_deleted(await prepare_account_deleted("a1", None))

    [event] = await list_lifecycle_events("a1")
    # A guessed death names its evidence, so survival can tell it from older guesses.
    assert event.extra == {"reason": "dead", "reason_source": "guess", "evidence": "session"}


@pytest.mark.asyncio
async def test_the_snapshot_is_the_one_taken_before_the_cascade() -> None:
    """Written after the account is gone, the row still carries the pre-delete user id."""
    await _account()
    pending = await prepare_account_deleted("a1", "dead")
    await delete_account("a1")

    await record_account_deleted(pending)

    [event] = await list_lifecycle_events("a1")
    assert event.tg_user_id == 4242


@pytest.mark.asyncio
async def test_a_failing_guess_prepares_nothing_and_does_not_raise(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    monkeypatch.setattr(lifecycle_journal, "fetch_account", _async_explode)

    with caplog.at_level(logging.WARNING, logger="services.lifecycle_journal"):
        pending = await prepare_account_deleted("a1", None)
        await record_account_deleted(pending)

    assert pending is None
    assert await list_lifecycle_events("a1") == []
    assert "delete-reason guess failed" in caplog.text


@pytest.mark.asyncio
async def test_a_failing_deletion_write_is_swallowed(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    await _account()
    pending = await prepare_account_deleted("a1", "dead")
    monkeypatch.setattr(lifecycle_journal, "append_lifecycle_event", _async_explode)

    with caplog.at_level(logging.WARNING, logger="services.lifecycle_journal"):
        await record_account_deleted(pending)

    assert "kind=deleted" in caplog.text
