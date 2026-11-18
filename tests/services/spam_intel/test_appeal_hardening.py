"""The appeal layer's hardening (audit F-010, F-019, F-020, F-021, F-022).

* F-010: a clean older than the filing never closes the newer appeal, even through a deferred call.
* F-022: a frozen or dead account cannot register an appeal on a stale limited reading.
* F-019: the card reads in a constant number of SELECTs and never writes; the loop's pass closes.
* F-020: the fleet's "out of turn" count is the checks that really were out of turn.
* F-021: the re-check time is one definition; a limit extended after the check promises no date.
"""

from __future__ import annotations

from datetime import UTC, date, datetime, timedelta
from typing import TYPE_CHECKING, Self

import pytest
from sqlalchemy import event

from core.config import settings
from core.db import _get_engine
from core.repositories.spam_appeals import fetch_latest_appeal, fetch_open_appeal
from schemas.spam_appeals import SpamAppeal
from schemas.spam_evidence import Verdict
from services import spam_intel
from services.spam_intel import _appeal, _appeal_close
from tests.services.spam_intel._appeal_support import (
    TODAY,
    appeal_clock,
    appeal_events,
    store_limited,
    store_reading,
)

if TYPE_CHECKING:
    from collections.abc import Awaitable, Callable

    from tests.services.spam_intel.conftest import Clock, FakeProbe

    MakeAccount = Callable[..., Awaitable[None]]

__all__ = ["appeal_clock", "appeal_events"]


# --- F-010 ------------------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_a_clean_taken_before_the_filing_does_not_close_the_newer_appeal(
    make_account: MakeAccount, clock: Clock
) -> None:
    await make_account("a1")
    await store_limited("a1", clock.now())
    clock.advance(minutes=2)  # the appeal is filed at 12:02
    await spam_intel.record_appeal_filed("a1", "anna")

    # A deferred close carrying the clean of 12:01 arrives afterwards.
    closed = await _appeal_close.close_on_clean("a1", clock.now() - timedelta(minutes=1))

    assert closed is False
    assert await fetch_open_appeal("a1") is not None
    assert (await spam_intel.appeal_state("a1")).status == "open"


@pytest.mark.asyncio
async def test_a_clean_taken_after_the_filing_closes_that_appeal_by_its_id(
    make_account: MakeAccount, clock: Clock
) -> None:
    await make_account("a1")
    await store_limited("a1", clock.now())
    await spam_intel.record_appeal_filed("a1", "anna")
    opened = await fetch_open_appeal("a1")
    assert opened is not None

    # Another appeal's id is not this one: nothing closes.
    assert not await _appeal_close.close_on_clean(
        "a1", clock.now() + timedelta(hours=1), appeal_id=opened.id + 1
    )
    assert await _appeal_close.close_on_clean(
        "a1", clock.now() + timedelta(hours=1), appeal_id=opened.id
    )
    assert await fetch_open_appeal("a1") is None


# --- F-022 ------------------------------------------------------------------------------------


@pytest.mark.asyncio
@pytest.mark.parametrize("status", ["frozen", "unauthorized", "session_error", "account_error"])
async def test_an_account_that_is_frozen_or_dead_cannot_register_an_appeal(
    make_account: MakeAccount, clock: Clock, status: str
) -> None:
    await make_account("a1", status=status)
    await store_limited("a1", clock.now())  # a stale limited reading, no new projection

    with pytest.raises(ValueError, match=spam_intel.NOT_LIMITED_REFUSAL):
        await spam_intel.record_appeal_filed("a1", "anna")

    assert await fetch_latest_appeal("a1") is None


@pytest.mark.asyncio
async def test_an_account_that_is_gone_cannot_register_an_appeal() -> None:
    with pytest.raises(ValueError, match=spam_intel.NOT_LIMITED_REFUSAL):
        await spam_intel.record_appeal_filed("ghost", "anna")


# --- F-019 ------------------------------------------------------------------------------------


class _Statements:
    """Every statement the engine runs while the block is open."""

    def __init__(self) -> None:
        self.sql: list[str] = []

    def __enter__(self) -> Self:
        event.listen(_get_engine(), "before_cursor_execute", self._seen)
        return self

    def __exit__(self, *_exc: object) -> None:
        event.remove(_get_engine(), "before_cursor_execute", self._seen)

    def _seen(self, _conn: object, _cursor: object, statement: str, *_rest: object) -> None:
        self.sql.append(statement.lstrip().split(None, 1)[0].upper())

    @property
    def selects(self) -> int:
        return self.sql.count("SELECT")

    @property
    def writes(self) -> list[str]:
        return [word for word in self.sql if word in {"INSERT", "UPDATE", "DELETE"}]


async def _open_appeals(make_account: MakeAccount, clock: Clock, count: int) -> list[str]:
    ids = [f"a{n}" for n in range(count)]
    for account_id in ids:
        await make_account(account_id)
        await store_limited(account_id, clock.now())
        await spam_intel.record_appeal_filed(account_id, "anna")
    return ids


@pytest.mark.asyncio
@pytest.mark.parametrize("count", [0, 1, 3, 6])
async def test_the_card_reads_in_a_constant_number_of_selects_and_writes_nothing(
    make_account: MakeAccount, clock: Clock, count: int
) -> None:
    ids = await _open_appeals(make_account, clock, count)
    if count:
        clock.advance(hours=2)
        await store_reading(ids[0], "clean", None, clock.now())  # an appeal a clean already ended
    await make_account("plain")

    with _Statements() as seen:
        cards = await spam_intel.card_signals([*ids, "plain"])

    assert seen.selects <= 5  # the same for 0, 1, 3 or 6 appeals
    assert seen.writes == []
    if count:
        assert cards[ids[0]].appeal_status == "closed"  # shown closed, not written closed
        assert await fetch_open_appeal(ids[0]) is not None
    assert all(cards[account_id].appeal_status == "open" for account_id in ids[1:])


@pytest.mark.asyncio
async def test_reading_the_appeal_state_and_list_writes_nothing(
    make_account: MakeAccount, clock: Clock
) -> None:
    ids = await _open_appeals(make_account, clock, 3)
    clock.advance(hours=2)
    await store_reading(ids[0], "clean", None, clock.now())

    with _Statements() as seen:
        state = await spam_intel.appeal_state(ids[0])
        listed = await spam_intel.open_appeal_states()

    assert seen.writes == []
    assert (state.status, state.closed_by_kind) == ("closed", "spambot_clean")
    assert [s.account_id for s in listed] == ids[1:]


@pytest.mark.asyncio
async def test_the_loops_pass_persists_what_a_stored_clean_already_ended(
    make_account: MakeAccount, clock: Clock
) -> None:
    ids = await _open_appeals(make_account, clock, 3)
    clock.advance(hours=2)
    await store_reading(ids[0], "clean", None, clock.now())

    assert await _appeal.close_ended_appeals() == 1

    assert await fetch_open_appeal(ids[0]) is None
    assert await fetch_open_appeal(ids[1]) is not None
    assert await _appeal.close_ended_appeals() == 0


# --- F-020 ------------------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_only_a_check_that_really_was_out_of_turn_counts_toward_the_warning(
    make_account: MakeAccount, probe: FakeProbe, clock: Clock, monkeypatch: pytest.MonkeyPatch
) -> None:
    del probe
    monkeypatch.setattr(settings.spam_intel, "out_of_turn_warn_count", 5)
    for n in range(5):
        await make_account(f"a{n}")
    # Five ordinary first checks on five accounts: not one is out of turn.
    for n in range(5):
        await spam_intel.operator_check(f"a{n}", confirm_out_of_turn=False)
    calm = await spam_intel.budget_status()
    assert (calm.probes_24h, calm.out_of_turn_24h, calm.out_of_turn_warned) == (5, 0, False)

    clock.advance(hours=2)  # past the 30-minute window, inside a day: now it is out of turn
    await spam_intel.operator_check("a0", confirm_out_of_turn=True)
    busy = await spam_intel.budget_status()
    assert (busy.probes_24h, busy.out_of_turn_24h) == (6, 1)


# --- F-021 ------------------------------------------------------------------------------------

_CFG = settings.spam_intel
_FILED = TODAY


def _appeal_row(until: date | None) -> SpamAppeal:
    return SpamAppeal(
        id=7, account_id="a1", filed_at=_FILED, filed_by="anna", verdict="limited", until_day=until
    )


def _limited_until(day: date, last_spambot_at: datetime | None) -> Verdict:
    ends = datetime(day.year, day.month, day.day, tzinfo=UTC) + timedelta(days=1, hours=1)
    return Verdict(
        status="limited", valid_until=ends, primary_kind="spambot_limited",
        last_spambot_at=last_spambot_at,
    )  # fmt: skip


def test_an_open_ended_appeal_is_rechecked_hours_after_filing() -> None:
    due = _appeal.recheck_at(_appeal_row(None), None, _CFG)
    assert due == _FILED + timedelta(hours=_CFG.appeal_recheck_hours)


def test_a_limit_extended_before_the_check_moves_the_recheck_to_the_new_end() -> None:
    first = date(2026, 11, 20)
    later = date(2026, 12, 5)
    appeal = _appeal_row(first)
    base = _appeal.recheck_at(appeal, None, _CFG)
    assert base is not None
    extended = _appeal.recheck_at(appeal, _limited_until(later, _FILED), _CFG)
    assert extended is not None
    assert extended > base  # nothing was asked since: the check waits for the new end
    assert extended.date() >= later


def test_a_limit_extended_by_the_check_itself_promises_no_second_recheck() -> None:
    """The check ran at the due time and the limit was extended: none will follow, none shown."""
    first = date(2026, 11, 20)
    appeal = _appeal_row(first)
    base = _appeal.recheck_at(appeal, None, _CFG)
    assert base is not None
    answered = _limited_until(date(2026, 12, 5), base + timedelta(minutes=1))

    assert _appeal.recheck_at(appeal, answered, _CFG) is None


@pytest.mark.asyncio
async def test_the_card_and_the_loop_agree_after_the_check_extended_the_limit(
    make_account: MakeAccount, clock: Clock, probe: FakeProbe
) -> None:
    from services.spam_intel import _appeal_recheck  # noqa: PLC0415 - one test needs the loop

    await make_account("a1")
    await store_limited("a1", clock.now(), detail="Your account is now limited until 20 Nov 2026.")
    await spam_intel.record_appeal_filed("a1", "anna")
    due = (await spam_intel.appeal_state("a1")).recheck_at
    assert due is not None
    clock.at = due + timedelta(minutes=1)
    probe.reply = "Your account is now limited until 5 Dec 2026."

    asked = await _appeal_recheck.run_appeal_rechecks()
    assert asked == 1

    state = await spam_intel.appeal_state("a1")
    assert state.status == "open"
    assert state.recheck_at is None  # the card promises nothing the loop will not do
    clock.advance(days=20)
    assert await _appeal_recheck.due_appeals(clock.now()) == []
