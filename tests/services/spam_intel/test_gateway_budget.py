"""The budget as the gateway applies it: spacing, caps, busy accounts, the operator's button."""

from __future__ import annotations

from datetime import timedelta
from typing import TYPE_CHECKING

import pytest

from core.config import settings
from core.db import get_spam_status, upsert_spam_status
from schemas.spam_status import SpamStatusVerdict
from services import _account_owner, spam_intel
from services.spam_intel import _gateway

if TYPE_CHECKING:
    from collections.abc import Awaitable, Callable

    from tests.services.spam_intel.conftest import Clock, Events, FakeProbe

    MakeAccount = Callable[..., Awaitable[None]]


@pytest.mark.asyncio
async def test_two_accounts_behind_one_proxy_are_not_asked_together(
    make_account: MakeAccount,
    probe: FakeProbe,
    clock: Clock,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(settings.spam_intel, "proxy_spacing_minutes", 120.0)
    await make_account("acc-1", proxy="shared")
    await make_account("acc-2", proxy="shared")
    await make_account("acc-3", proxy="other")
    first = await spam_intel.ask("acc-1", force=True)
    second = await spam_intel.ask("acc-2", force=True)
    elsewhere = await spam_intel.ask("acc-3", force=True)
    assert first.served_from == "probe"
    assert (second.served_from, second.reason) == ("deferred", "proxy_spacing")
    assert elsewhere.served_from == "probe"
    clock.advance(minutes=121)
    later = await spam_intel.ask("acc-2", force=True)
    assert later.served_from == "probe"
    assert probe.calls == ["acc-1", "acc-3", "acc-2"]


@pytest.mark.asyncio
async def test_any_two_probes_in_the_fleet_keep_a_gap(
    make_account: MakeAccount,
    probe: FakeProbe,
    clock: Clock,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The 11-in-84-seconds burst on production cannot recur: the next probe waits its gap."""
    monkeypatch.setattr(settings.spam_intel, "fleet_gap_min_seconds", 180.0)
    monkeypatch.setattr(settings.spam_intel, "fleet_gap_max_seconds", 600.0)
    for index in range(1, 5):
        await make_account(f"acc-{index}")
    answers = [await spam_intel.ask(f"acc-{index}", force=True) for index in range(1, 5)]
    assert [a.served_from for a in answers] == ["probe", "deferred", "deferred", "deferred"]
    assert {a.reason for a in answers[1:]} == {"fleet_spacing"}
    assert answers[1].retry_at is not None
    clock.advance(seconds=181)
    assert (await spam_intel.ask("acc-2", force=True)).served_from == "probe"
    assert (await spam_intel.ask("acc-3", force=True)).served_from == "deferred"
    assert len(probe.calls) == 2


@pytest.mark.asyncio
async def test_the_soft_cap_waits_routine_asks_and_warns_for_decisions(
    make_account: MakeAccount,
    events: Events,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(settings.spam_intel, "fleet_soft_cap", 2)
    monkeypatch.setattr(settings.spam_intel, "fleet_hard_cap", 4)
    for index in range(1, 6):
        await make_account(f"acc-{index}")
    await spam_intel.ask("acc-1", force=True)
    await spam_intel.ask("acc-2", force=True)
    routine = await spam_intel.ask("acc-3")
    assert (routine.served_from, routine.reason) == ("deferred", "fleet_soft_cap")
    with spam_intel.caller_scope("warming.handoff"):
        decision = await spam_intel.ask("acc-3", force=True)
    assert decision.served_from == "probe"
    warned = events.of("spam_intel_budget_exceeded")
    assert [(w["cap"], w["limit"]) for w in warned] == [("soft", 2)]


@pytest.mark.asyncio
async def test_the_hard_cap_stops_everyone_and_raises_the_notification_event(
    make_account: MakeAccount,
    probe: FakeProbe,
    events: Events,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(settings.spam_intel, "fleet_soft_cap", 1)
    monkeypatch.setattr(settings.spam_intel, "fleet_hard_cap", 2)
    for index in range(1, 5):
        await make_account(f"acc-{index}")
    await spam_intel.ask("acc-1", force=True)
    await spam_intel.ask("acc-2", force=True)
    blocked = await spam_intel.ask("acc-3", force=True)
    operator = await spam_intel.operator_check("acc-4", confirm_out_of_turn=True)
    assert (blocked.served_from, blocked.reason) == ("deferred", "fleet_hard_cap")
    assert operator.reason == "fleet_hard_cap"
    assert probe.calls == ["acc-1", "acc-2"]
    assert any(e["cap"] == "hard" for e in events.of("spam_intel_budget_exceeded"))


@pytest.mark.asyncio
async def test_one_account_cannot_be_probed_past_its_daily_cap(
    make_account: MakeAccount,
    clock: Clock,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(settings.spam_intel, "account_daily_cap", 2)
    await make_account("acc-1")
    for _ in range(2):
        assert (await spam_intel.ask("acc-1", force=True)).served_from == "probe"
        clock.advance(hours=1)
    capped = await spam_intel.ask("acc-1", force=True)
    assert (capped.served_from, capped.reason) == ("deferred", "account_daily_cap")
    clock.advance(hours=24)
    assert (await spam_intel.ask("acc-1", force=True)).served_from == "probe"


@pytest.mark.asyncio
async def test_an_account_another_feature_holds_waits_but_its_own_feature_asks(
    make_account: MakeAccount, probe: FakeProbe
) -> None:
    await make_account("acc-1")
    assert _account_owner.try_claim("acc-1", "chat_broadcast", "run-1") is None
    outsider = await spam_intel.ask("acc-1", force=True, holder="warming")
    assert (outsider.served_from, outsider.reason) == ("deferred", "busy")
    assert probe.calls == []
    owner = await spam_intel.ask("acc-1", force=True, holder="chat_broadcast")
    assert owner.served_from == "probe"


@pytest.mark.asyncio
async def test_a_lent_account_waits_for_everyone(make_account: MakeAccount) -> None:
    await make_account("acc-1")
    assert _account_owner.lend("acc-1", "search-1")
    answer = await spam_intel.ask("acc-1", force=True, holder="warming")
    assert (answer.served_from, answer.reason) == ("deferred", "busy")


@pytest.mark.asyncio
async def test_routine_asks_wait_out_a_fresh_start_but_decisions_do_not(
    make_account: MakeAccount, clock: Clock
) -> None:
    await make_account("acc-1")
    _gateway._STARTED = clock.now() - timedelta(minutes=1)
    routine = await spam_intel.ask("acc-1")
    assert (routine.served_from, routine.reason) == ("deferred", "startup_quiet")
    with spam_intel.caller_scope("warming.quarantine"):
        decision = await spam_intel.ask("acc-1", force=True)
    assert decision.served_from == "probe"


@pytest.mark.asyncio
async def test_the_operator_gets_a_fresh_reading_when_nothing_is_recent(
    make_account: MakeAccount,
) -> None:
    await make_account("acc-1")
    verdict = await spam_intel.operator_check("acc-1", confirm_out_of_turn=False)
    assert (verdict.status, verdict.served_from) == ("clean", "probe")


@pytest.mark.asyncio
async def test_an_out_of_turn_click_without_confirmation_keeps_the_stored_status(
    make_account: MakeAccount, probe: FakeProbe, clock: Clock
) -> None:
    await make_account("acc-1")
    await spam_intel.ask("acc-1", force=True)
    clock.advance(hours=3)
    held = await spam_intel.operator_check("acc-1", confirm_out_of_turn=False)
    assert (held.status, held.served_from, held.reason) == (
        "clean",
        "refused",
        "needs_confirmation",
    )
    assert probe.calls == ["acc-1"]
    confirmed = await spam_intel.operator_check("acc-1", confirm_out_of_turn=True)
    assert confirmed.served_from == "probe"


@pytest.mark.asyncio
async def test_a_click_inside_the_window_is_handed_the_last_reading(
    make_account: MakeAccount, probe: FakeProbe, clock: Clock
) -> None:
    await make_account("acc-1")
    await spam_intel.ask("acc-1", force=True)
    clock.advance(minutes=5)
    for _ in range(3):  # a nervous double-click, and one more
        verdict = await spam_intel.operator_check("acc-1", confirm_out_of_turn=True)
        assert (verdict.status, verdict.served_from) == ("clean", "coalesced")
    assert probe.calls == ["acc-1"]


@pytest.mark.asyncio
async def test_the_operator_has_a_daily_cap_of_its_own(
    make_account: MakeAccount,
    clock: Clock,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(settings.spam_intel, "operator_daily_cap", 2)
    await make_account("acc-1")
    for _ in range(2):
        assert (
            await spam_intel.operator_check("acc-1", confirm_out_of_turn=True)
        ).served_from == "probe"
        clock.advance(hours=1)
    spent = await spam_intel.operator_check("acc-1", confirm_out_of_turn=True)
    assert (spent.served_from, spent.reason) == ("refused", "operator_daily_cap")
    stored = await get_spam_status("acc-1")
    assert stored is not None
    assert spent.status == stored.status


@pytest.mark.asyncio
async def test_the_operator_is_not_held_by_a_busy_account_or_the_proxy_spacing(
    make_account: MakeAccount,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(settings.spam_intel, "proxy_spacing_minutes", 120.0)
    await make_account("acc-1", proxy="shared")
    await make_account("acc-2", proxy="shared")
    await spam_intel.ask("acc-1", force=True)
    assert _account_owner.try_claim("acc-2", "warming", "run-1") is None
    verdict = await spam_intel.operator_check("acc-2", confirm_out_of_turn=True)
    assert verdict.served_from == "probe"


@pytest.mark.asyncio
async def test_an_operator_refusal_with_nothing_stored_is_an_unknown_that_says_why(
    make_account: MakeAccount,
) -> None:
    await make_account("acc-1", proxy=None)
    verdict = await spam_intel.operator_check("acc-1", confirm_out_of_turn=True)
    assert (verdict.status, verdict.served_from, verdict.reason) == (
        "unknown",
        "refused",
        "no_proxy",
    )


@pytest.mark.asyncio
async def test_a_passive_limited_row_is_a_reading_for_the_cache_but_not_for_coalescing(
    make_account: MakeAccount, clock: Clock
) -> None:
    """Masstagging's own ``limited`` write is a fresh cache entry, never a probe in the ledger."""
    await make_account("acc-1")
    await upsert_spam_status(
        SpamStatusVerdict(
            account_id="acc-1",
            status="limited",
            detail="peer_flood",
            checked_at=clock.now().isoformat(),
        )
    )
    cached = await spam_intel.ask("acc-1")
    assert (cached.status, cached.served_from) == ("limited", "cache")
    forced = await spam_intel.ask("acc-1", force=True)
    assert (forced.status, forced.served_from) == ("clean", "probe")
