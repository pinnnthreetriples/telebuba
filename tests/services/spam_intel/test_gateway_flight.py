"""What a caller who joins a probe in flight may be handed, and what a dead probe leaves behind.

Single-flight (design 5.5 rule 3) shares ONE real probe. It must not share anything else: a
cache hit, a passive answer or a budget refusal belongs to the request that produced it, and a
forced, measurement or operator ask exists to get a real answer. A probe that was cancelled or
crashed answered nothing, so the reading stored before it is not "the last reading" any more.
"""

from __future__ import annotations

import asyncio
from datetime import timedelta
from typing import TYPE_CHECKING

import pytest

from core.config import settings
from core.db import _get_engine, upsert_spam_status
from core.repositories.spam_probe_ledger import fetch_probe_state
from schemas.spam_status import SpamStatusVerdict
from services import spam_intel, spam_status
from tests.services.spam_intel.conftest import LIMITED_TEXT

if TYPE_CHECKING:
    from collections.abc import Awaitable, Callable

    from schemas.spam_status import SpamStatusKind
    from tests.services.spam_intel.conftest import Clock, FakeProbe

    MakeAccount = Callable[..., Awaitable[None]]


async def _stored(
    account: str, clock: Clock, status: SpamStatusKind = "clean", hours: float = 1.0
) -> None:
    await upsert_spam_status(
        SpamStatusVerdict(
            account_id=account,
            status=status,
            checked_at=(clock.now() - timedelta(hours=hours)).isoformat(),
        )
    )


@pytest.mark.asyncio
async def test_a_forced_ask_does_not_inherit_a_cache_hit_of_a_routine_ask(
    make_account: MakeAccount, probe: FakeProbe, clock: Clock
) -> None:
    await make_account("acc-1")
    await _stored("acc-1", clock)
    routine = asyncio.create_task(spam_intel.ask("acc-1", purpose="routine"))
    await asyncio.sleep(0)
    forced = asyncio.create_task(spam_intel.ask("acc-1", force=True, purpose="decision"))
    first, second = await asyncio.gather(routine, forced)
    assert first.served_from == "cache"
    assert (second.served_from, second.status) == ("probe", "clean")
    assert probe.calls == ["acc-1"]


@pytest.mark.asyncio
async def test_a_forced_ask_does_not_inherit_a_passive_answer(
    make_account: MakeAccount, probe: FakeProbe, clock: Clock, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(settings.spam_intel, "passive_decisions", True)
    await make_account("acc-1")
    seen = clock.now() - timedelta(hours=40)
    await spam_intel.record_evidence("acc-1", "spambot_clean", source="spambot", observed_at=seen)
    await _stored("acc-1", clock, hours=40)
    routine = asyncio.create_task(spam_intel.ask("acc-1", purpose="routine"))
    await asyncio.sleep(0)
    forced = asyncio.create_task(spam_intel.ask("acc-1", force=True, purpose="decision"))
    first, second = await asyncio.gather(routine, forced)
    assert first.served_from == "evidence"
    assert second.served_from == "probe"
    assert probe.calls == ["acc-1"]


@pytest.mark.asyncio
async def test_a_measurement_ask_is_never_answered_from_the_cache(
    make_account: MakeAccount, probe: FakeProbe, clock: Clock
) -> None:
    """The survival window's product is a real answer, with its own ``spambot_verdict``."""
    await make_account("acc-1")
    await _stored("acc-1", clock)
    with spam_intel.caller_scope("survival.window"):
        verdict = await spam_intel.ask("acc-1")
    assert (verdict.served_from, probe.calls) == ("probe", ["acc-1"])


@pytest.mark.asyncio
async def test_a_measurement_ask_is_never_answered_from_evidence(
    make_account: MakeAccount, probe: FakeProbe, clock: Clock, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(settings.spam_intel, "passive_decisions", True)
    await make_account("acc-1")
    await spam_intel.record_evidence(
        "acc-1", "spambot_clean", source="spambot", observed_at=clock.now() - timedelta(hours=40)
    )
    await _stored("acc-1", clock, hours=40)
    with spam_intel.caller_scope("survival.window"):
        verdict = await spam_intel.ask("acc-1")
    assert (verdict.served_from, probe.calls) == ("probe", ["acc-1"])


@pytest.mark.asyncio
async def test_a_routine_ask_still_gets_the_cache_and_still_shares_a_real_probe(
    make_account: MakeAccount, probe: FakeProbe, clock: Clock
) -> None:
    await make_account("acc-1")
    await make_account("acc-2")
    await _stored("acc-1", clock)
    assert (await spam_intel.ask("acc-1")).served_from == "cache"
    probe.delay = 0.05
    answers = await asyncio.gather(
        spam_intel.ask("acc-2", force=True), spam_intel.ask("acc-2"), spam_intel.ask("acc-2")
    )
    assert sorted(a.served_from or "" for a in answers) == ["coalesced", "coalesced", "probe"]
    assert probe.calls == ["acc-2"]


@pytest.mark.asyncio
async def test_a_joiner_does_not_inherit_a_budget_deferral_meant_for_another_purpose(
    make_account: MakeAccount, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Only a ROUTINE ask waits out a fresh start; a decision joining it must ask for itself."""
    monkeypatch.setattr(spam_intel._gateway, "_STARTED", spam_intel._gateway.now())
    await make_account("acc-1")
    routine = asyncio.create_task(spam_intel.ask("acc-1", purpose="routine"))
    await asyncio.sleep(0)
    decision = asyncio.create_task(spam_intel.ask("acc-1", purpose="decision"))
    first, second = await asyncio.gather(routine, decision)
    assert (first.served_from, first.reason) == ("deferred", "startup_quiet")
    assert second.served_from == "probe"


@pytest.mark.asyncio
async def test_a_cancelled_leader_does_not_hand_its_waiters_the_old_clean_as_fresh(
    make_account: MakeAccount, probe: FakeProbe, clock: Clock
) -> None:
    await make_account("acc-1")
    await _stored("acc-1", clock, hours=50)
    probe.delay = 5.0
    leader = asyncio.create_task(spam_intel.ask("acc-1", force=True))
    await asyncio.sleep(0.1)  # the attempt is on the ledger and the question is out
    waiter = asyncio.create_task(spam_intel.ask("acc-1", force=True))
    await asyncio.sleep(0)
    leader.cancel()
    with pytest.raises(asyncio.CancelledError):
        await leader
    answer = await waiter
    assert answer.status == "unknown"
    assert answer.served_from in {"coalesced", "deferred"}
    assert probe.calls == ["acc-1"]  # nothing was asked a second time
    with _get_engine().connect() as connection:
        rows = connection.exec_driver_sql(
            "SELECT outcome, error_type FROM spam_probe_attempts"
        ).all()
    assert [tuple(r) for r in rows] == [("error", "CancelledError")]


@pytest.mark.asyncio
async def test_a_crashed_probe_makes_the_old_clean_unknown_inside_the_window(
    make_account: MakeAccount, clock: Clock, monkeypatch: pytest.MonkeyPatch
) -> None:
    await make_account("acc-1")
    await _stored("acc-1", clock, hours=50)

    async def _boom(account_id: str, *, previous: object = None) -> object:  # noqa: ARG001
        msg = "executor blew up"
        raise RuntimeError(msg)

    monkeypatch.setattr(spam_status, "probe_and_store", _boom)
    with pytest.raises(RuntimeError, match="executor blew up"):
        await spam_intel.ask("acc-1", force=True)
    clock.advance(minutes=5)
    again = await spam_intel.ask("acc-1", force=True)
    assert (again.status, again.served_from, again.detail) == (
        "unknown",
        "coalesced",
        "RuntimeError",
    )
    # A crash is not a verdict of the account: no streak, no backoff.
    assert await fetch_probe_state("acc-1") is None


@pytest.mark.asyncio
async def test_a_real_reading_after_the_dead_attempt_is_served_again(
    make_account: MakeAccount, probe: FakeProbe, clock: Clock, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The uncertainty ends with the next real answer, not with the passing of the window."""
    await make_account("acc-1")
    real = spam_status.probe_and_store
    armed = {"on": True}

    async def _once(
        account_id: str, *, previous: SpamStatusVerdict | None = None, **_plan: object
    ) -> object:
        if armed["on"]:
            armed["on"] = False
            msg = "executor blew up"
            raise RuntimeError(msg)
        return await real(account_id, previous=previous)

    monkeypatch.setattr(spam_status, "probe_and_store", _once)
    with pytest.raises(RuntimeError, match="executor blew up"):
        await spam_intel.ask("acc-1", force=True)
    clock.advance(minutes=settings.spam_intel.coalesce_minutes + 1)
    probe.reply = LIMITED_TEXT
    answer = await spam_intel.ask("acc-1", force=True)
    assert (answer.served_from, answer.status) == ("probe", "limited")
    clock.advance(minutes=5)
    again = await spam_intel.ask("acc-1", force=True)
    assert (again.served_from, again.status) == ("coalesced", "limited")
