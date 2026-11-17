"""The appeal record (docs/spam-intel.md 5.12): who, when, one open row, closed by a ``clean``."""

from __future__ import annotations

from datetime import date, timedelta
from typing import TYPE_CHECKING

import pytest

from core.repositories.spam_appeals import fetch_latest_appeal
from core.repositories.spam_status import list_projections_by_ids
from services import spam_intel
from services.spam_intel import _appeal, _appeal_close
from tests.services.spam_intel._appeal_support import (
    TODAY,
    appeal_clock,
    appeal_events,
    store_limited,
    store_reading,
)
from tests.services.spam_intel.conftest import CLEAN_TEXT, FakeProbe

if TYPE_CHECKING:
    from collections.abc import Awaitable, Callable

    from tests.services.spam_intel.conftest import Clock, Events

    MakeAccount = Callable[..., Awaitable[None]]

__all__ = ["appeal_clock", "appeal_events"]


@pytest.mark.asyncio
async def test_filing_records_who_when_and_what_the_verdict_said(
    make_account: MakeAccount, clock: Clock, events: Events
) -> None:
    await make_account("a1")
    await store_limited("a1", clock.now())

    state = await spam_intel.record_appeal_filed("a1", "anna")

    assert state.status == "open"
    assert (state.account_id, state.filed_by, state.filed_at) == ("a1", "anna", TODAY)
    assert (state.filed_verdict, state.filed_until_day) == ("limited", date(2026, 11, 29))
    assert state.closed_at is None
    assert events.of("spam_intel_appeal_filed") == [{"verdict": "limited", "dated": True}]
    stored = await fetch_latest_appeal("a1")
    assert stored is not None
    assert (stored.filed_by, stored.until_day) == ("anna", date(2026, 11, 29))


@pytest.mark.asyncio
async def test_an_open_ended_limit_records_no_end_day(
    make_account: MakeAccount, clock: Clock
) -> None:
    await make_account("a1")
    await store_limited("a1", clock.now(), detail="Your account is limited.")

    state = await spam_intel.record_appeal_filed("a1", "anna")

    assert state.filed_until_day is None


@pytest.mark.asyncio
async def test_a_second_press_changes_nothing_not_who_and_not_when(
    make_account: MakeAccount, clock: Clock, events: Events
) -> None:
    await make_account("a1")
    await store_limited("a1", clock.now())
    first = await spam_intel.record_appeal_filed("a1", "anna")

    clock.advance(hours=3)
    again = await spam_intel.record_appeal_filed("a1", "boris")

    assert again == first
    assert (again.filed_by, again.filed_at) == ("anna", TODAY)
    assert len(events.of("spam_intel_appeal_filed")) == 1
    assert [a.account_id for a in await spam_intel.open_appeal_states()] == ["a1"]


@pytest.mark.asyncio
@pytest.mark.parametrize("status", ["clean", "frozen"])
async def test_there_is_nothing_to_appeal_on_a_clean_or_frozen_account(
    make_account: MakeAccount, clock: Clock, status: str
) -> None:
    await make_account("a1")
    if status == "clean":
        await store_reading("a1", "clean", None, clock.now())
    else:
        await spam_intel.record_evidence("a1", "freeze_seen", source="session_check")

    with pytest.raises(ValueError, match=spam_intel.NOT_LIMITED_REFUSAL):
        await spam_intel.record_appeal_filed("a1", "anna")

    assert (await spam_intel.appeal_state("a1")).status == "none"


@pytest.mark.asyncio
async def test_an_unknown_verdict_may_be_appealed_and_is_recorded_as_unknown(
    make_account: MakeAccount,
) -> None:
    await make_account("a1")

    state = await spam_intel.record_appeal_filed("a1", "anna")

    assert (state.status, state.filed_verdict) == ("open", "unknown")


@pytest.mark.asyncio
async def test_a_persisted_spambot_clean_closes_the_appeal(
    make_account: MakeAccount, clock: Clock, probe: FakeProbe, events: Events
) -> None:
    await make_account("a1")
    await store_limited("a1", clock.now())
    await spam_intel.record_appeal_filed("a1", "anna")
    clock.advance(hours=3)
    probe.reply = CLEAN_TEXT

    answer = await spam_intel.ask("a1", force=True, purpose="decision", caller="test")

    assert (answer.status, answer.served_from) == ("clean", "probe")
    state = await spam_intel.appeal_state("a1")
    assert state.status == "closed"
    assert (state.closed_by_kind, state.closed_at) == ("spambot_clean", clock.now())
    assert (state.filed_by, state.recheck_at) == ("anna", None)
    assert await spam_intel.open_appeal("a1") is None
    assert events.of("spam_intel_appeal_closed") == [{"by": "spambot_clean"}]


@pytest.mark.asyncio
async def test_a_limited_answer_leaves_the_appeal_open(
    make_account: MakeAccount, clock: Clock, probe: FakeProbe
) -> None:
    await make_account("a1")
    await store_limited("a1", clock.now())
    await spam_intel.record_appeal_filed("a1", "anna")
    clock.advance(hours=3)
    probe.reply = "Your account is now limited until 30 Nov 2026."

    await spam_intel.ask("a1", force=True, purpose="decision", caller="test")

    opened = await spam_intel.open_appeal("a1")
    assert opened is not None
    assert opened.closed_at is None


@pytest.mark.asyncio
async def test_a_passive_clean_never_closes_the_appeal(
    make_account: MakeAccount, clock: Clock
) -> None:
    """Owner decision D4: only a persisted @SpamBot clean says the block is gone."""
    await make_account("a1")
    await store_limited("a1", clock.now())
    await spam_intel.record_appeal_filed("a1", "anna")
    clock.advance(hours=3)

    await spam_intel.record_evidence("a1", "dm_stranger_ok", source="outreach")
    await spam_intel.record_evidence("a1", "join_ok", source="execute")

    assert (await spam_intel.appeal_state("a1")).status == "open"
    row = (await list_projections_by_ids(["a1"]))["a1"]
    assert row.legacy is not None
    assert row.legacy.status == "limited"


@pytest.mark.asyncio
async def test_a_clean_stored_after_filing_by_another_path_is_caught_up(
    make_account: MakeAccount, clock: Clock
) -> None:
    await make_account("a1")
    await store_limited("a1", clock.now())
    await spam_intel.record_appeal_filed("a1", "anna")
    clock.advance(hours=5)
    # A reading stored by a path that did not go through the gateway's store hook.
    await store_reading("a1", "clean", None, clock.now())

    state = await spam_intel.appeal_state("a1")

    assert (state.status, state.closed_by_kind) == ("closed", "spambot_clean")
    assert state.closed_at == clock.now()


@pytest.mark.asyncio
async def test_a_clean_older_than_the_filing_does_not_close_it(
    make_account: MakeAccount, clock: Clock
) -> None:
    await make_account("a1")
    await store_reading("a1", "clean", None, clock.now() - timedelta(days=2))
    await store_limited("a1", clock.now() - timedelta(days=1))
    await spam_intel.record_appeal_filed("a1", "anna")
    # The stored reading is now the (newer) limited one; put an old clean back to prove order.
    await store_reading("a1", "clean", None, clock.now() - timedelta(hours=1))

    assert (await spam_intel.appeal_state("a1")).status == "open"


@pytest.mark.asyncio
async def test_closing_never_raises_into_the_probe(
    make_account: MakeAccount, monkeypatch: pytest.MonkeyPatch
) -> None:
    await make_account("a1")

    async def _boom(*_args: object, **_kwargs: object) -> bool:
        message = "database is locked"
        raise RuntimeError(message)

    monkeypatch.setattr(_appeal_close, "close_open_appeal", _boom)

    assert await _appeal_close.close_on_clean("a1") is False


@pytest.mark.asyncio
async def test_no_appeal_reads_as_none_and_a_new_one_may_follow_a_closed_one(
    make_account: MakeAccount, clock: Clock
) -> None:
    await make_account("a1")
    assert (await spam_intel.appeal_state("a1")).status == "none"

    await store_limited("a1", clock.now())
    first = await spam_intel.record_appeal_filed("a1", "anna")
    clock.advance(hours=2)
    await spam_intel.ask("a1", force=True, purpose="decision", caller="test")  # clean (probe)
    assert (await spam_intel.appeal_state("a1")).status == "closed"

    clock.advance(days=3)
    await store_limited("a1", clock.now())
    second = await spam_intel.record_appeal_filed("a1", "boris")

    assert second.status == "open"
    assert (second.filed_by, second.filed_at) == ("boris", clock.now())
    assert second.filed_at != first.filed_at
    assert _appeal.NOT_LIMITED_REFUSAL == "spam_appeal_not_limited"
