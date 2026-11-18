"""The account card's read model and the Spam-check button (docs/spam-intel.md 5.10, T14)."""

from __future__ import annotations

from datetime import timedelta
from typing import TYPE_CHECKING, get_args

import pytest

from core.config import settings
from schemas.spam_evidence import EvidenceKind
from services import spam_intel
from services.spam_intel import _card
from tests.services.spam_intel._appeal_support import (
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


def test_every_evidence_kind_has_a_declared_source_or_none() -> None:
    """The enumerated source is total: a new kind falls to ``none`` unless it is mapped."""
    named = {kind for kind in get_args(EvidenceKind) if kind in _card._SOURCE}
    assert named == {
        "spambot_limited",
        "spambot_clean",
        "spambot_unreadable",
        "spambot_reviewing",
        "peer_flood_send",
        "peer_flood_other",
        "user_banned_in_channel",
        "freeze_seen",
    }


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("kind", "source"),
    [
        ("peer_flood_send", "peer_flood"),
        ("peer_flood_other", "peer_flood"),
        ("user_banned_in_channel", "chat_ban"),
        ("freeze_seen", "freeze"),
        ("spambot_limited", "spambot"),
    ],
)
async def test_the_source_and_its_instant_follow_the_deciding_observation(
    make_account: MakeAccount, clock: Clock, kind: EvidenceKind, source: str
) -> None:
    await make_account("a1")
    seen = clock.now() - timedelta(hours=2)
    await spam_intel.record_evidence("a1", kind, source="execute", observed_at=seen)

    card = (await spam_intel.card_signals(["a1"]))["a1"]

    assert card.source == source
    assert card.observed_at == seen
    assert card.verdict == ("frozen" if kind == "freeze_seen" else "limited")


@pytest.mark.asyncio
async def test_an_account_nobody_looked_at_has_no_source_and_no_noise(
    make_account: MakeAccount,
) -> None:
    await make_account("a1")
    card = (await spam_intel.card_signals(["a1"]))["a1"]
    assert (card.verdict, card.source, card.observed_at) == ("unknown", "none", None)
    assert (card.probes_7d, card.next_probe_at, card.last_probe_at) == (0, None, None)
    assert (card.out_of_turn, card.tos_restricted, card.appeal_status) == (False, False, "none")


@pytest.mark.asyncio
async def test_a_dated_limit_carries_its_end_and_an_undated_one_has_none(
    make_account: MakeAccount, clock: Clock
) -> None:
    await make_account("dated")
    await make_account("open_ended")
    until = clock.now() + timedelta(days=3)
    await spam_intel.record_evidence(
        "dated", "spambot_limited", source="spambot", expires_at=until, detail="2026-11-20"
    )
    await spam_intel.record_evidence("open_ended", "spambot_limited", source="spambot")

    cards = await spam_intel.card_signals(["dated", "open_ended"])

    assert cards["dated"].valid_until == until
    assert cards["open_ended"].valid_until is None
    assert cards["open_ended"].verdict == "limited"
    assert cards["open_ended"].confidence == "high"


@pytest.mark.asyncio
async def test_tos_restricted_is_a_flag_beside_the_verdict_never_a_spam_limit(
    make_account: MakeAccount, clock: Clock
) -> None:
    await make_account("a1")
    await spam_intel.ask("a1", force=True)  # a clean @SpamBot answer
    await spam_intel.record_evidence(
        "a1", "tos_restricted", source="session_check", detail="platform-all"
    )
    clock.advance(minutes=1)

    card = (await spam_intel.card_signals(["a1"]))["a1"]

    assert (card.verdict, card.source) == ("clean", "spambot")
    assert (card.tos_restricted, card.tos_reason) == (True, "platform-all")


@pytest.mark.asyncio
async def test_probe_counts_the_last_probe_and_the_next_moment_come_from_the_ledger(
    make_account: MakeAccount, probe: FakeProbe, clock: Clock
) -> None:
    await make_account("a1")
    await make_account("a2")
    first = clock.now()
    await spam_intel.ask("a1", force=True)
    clock.advance(hours=2)
    await spam_intel.ask("a1", force=True, purpose="decision")
    clock.advance(minutes=10)
    second = clock.now() - timedelta(minutes=10)

    cards = await spam_intel.card_signals(["a1", "a2"])

    assert cards["a1"].probes_7d == 2
    assert cards["a1"].last_probe_at == second
    assert cards["a1"].last_probe_at != first
    # 10 minutes into the window: not yet possible, and the card says when.
    assert cards["a1"].next_probe_at == second + timedelta(minutes=30)
    assert cards["a1"].out_of_turn is False
    assert cards["a2"].probes_7d == 0
    assert probe.calls == ["a1", "a1"]


@pytest.mark.asyncio
async def test_out_of_turn_is_between_the_window_and_a_day(
    make_account: MakeAccount, probe: FakeProbe, clock: Clock
) -> None:
    del probe
    await make_account("a1")
    await spam_intel.ask("a1", force=True)
    flags = []
    for step in (timedelta(minutes=5), timedelta(hours=1), timedelta(hours=22), timedelta(hours=1)):
        clock.advance(seconds=step.total_seconds())
        card = (await spam_intel.card_signals(["a1"]))["a1"]
        flags.append((card.out_of_turn, card.next_probe_at is None))
    # inside the window; out of turn; still out of turn; a day on.
    assert flags == [(False, False), (True, True), (True, True), (False, True)]


@pytest.mark.asyncio
async def test_one_bulk_read_serves_any_number_of_accounts(
    make_account: MakeAccount, monkeypatch: pytest.MonkeyPatch
) -> None:
    """No query per account: each ledger and appeal read happens once for the whole page."""
    calls = {"activity": 0, "states": 0}
    real_activity, real_states = _card.probe_activity, _card.list_probe_states

    async def _activity(*args: object) -> object:
        calls["activity"] += 1
        return await real_activity(*args)  # ty: ignore[invalid-argument-type]

    async def _states(*args: object) -> object:
        calls["states"] += 1
        return await real_states(*args)  # ty: ignore[invalid-argument-type]

    monkeypatch.setattr(_card, "probe_activity", _activity)
    monkeypatch.setattr(_card, "list_probe_states", _states)
    ids = [f"a{n}" for n in range(6)]
    for account_id in ids:
        await make_account(account_id)

    cards = await spam_intel.card_signals(ids)

    assert set(cards) == set(ids)
    assert calls == {"activity": 1, "states": 1}
    assert await spam_intel.card_signals([]) == {}


@pytest.mark.asyncio
async def test_the_appeal_summary_is_open_then_closed_with_who_and_when(
    make_account: MakeAccount, clock: Clock
) -> None:
    await make_account("a1")
    await store_limited("a1", clock.now())
    await spam_intel.record_appeal_filed("a1", "anna")

    open_card = (await spam_intel.card_signals(["a1"]))["a1"]
    assert (open_card.appeal_status, open_card.appeal_filed_by) == ("open", "anna")
    assert open_card.appeal_filed_at == clock.now()

    clock.advance(days=1)
    await store_reading("a1", "clean", None, clock.now())
    closed_card = (await spam_intel.card_signals(["a1"]))["a1"]
    assert (closed_card.appeal_status, closed_card.appeal_filed_by) == ("closed", "anna")


@pytest.mark.asyncio
async def test_the_fleet_budget_counts_probes_against_the_caps(
    make_account: MakeAccount, probe: FakeProbe, clock: Clock, monkeypatch: pytest.MonkeyPatch
) -> None:
    del probe
    monkeypatch.setattr(settings.spam_intel, "fleet_soft_cap", 3)
    monkeypatch.setattr(settings.spam_intel, "fleet_hard_cap", 6)
    monkeypatch.setattr(settings.spam_intel, "out_of_turn_warn_count", 2)
    for n in range(3):
        await make_account(f"a{n}")
    calm = await spam_intel.budget_status()
    assert (calm.probes_24h, calm.daily_cap_reached, calm.out_of_turn_warned) == (0, False, False)

    await spam_intel.ask("a0", force=True)
    await spam_intel.ask("a1", force=True)
    await spam_intel.operator_check("a2", confirm_out_of_turn=True)
    clock.advance(hours=1)
    await spam_intel.operator_check("a2", confirm_out_of_turn=True)

    busy = await spam_intel.budget_status()
    # Four probes; only the SECOND operator check on ``a2`` was out of turn (an account probed an
    # hour before): the first was an ordinary click and no longer counts (audit F-020).
    assert (busy.probes_24h, busy.out_of_turn_24h) == (4, 1)
    assert (busy.soft_cap, busy.hard_cap, busy.out_of_turn_warn_at) == (3, 6, 2)
    assert (busy.daily_cap_reached, busy.out_of_turn_warned) == (True, False)

    clock.advance(hours=25)  # the window rolls: everything aged out
    quiet = await spam_intel.budget_status()
    assert (quiet.probes_24h, quiet.daily_cap_reached) == (0, False)


@pytest.mark.asyncio
async def test_an_unconfirmed_out_of_turn_check_is_refused_and_a_confirmed_one_probes(
    make_account: MakeAccount, probe: FakeProbe, clock: Clock
) -> None:
    await make_account("a1")
    await spam_intel.ask("a1", force=True)
    clock.advance(hours=3)

    with pytest.raises(spam_intel.OutOfTurnConfirmationError) as refused:
        await spam_intel.check_for_operator("a1", confirm_out_of_turn=False)
    assert refused.value.code == spam_intel.NEEDS_CONFIRMATION
    assert probe.calls == ["a1"]  # nothing was asked

    result = await spam_intel.check_for_operator("a1", confirm_out_of_turn=True)
    assert (result.status, result.served_from, result.probed) == ("clean", "probe", True)
    assert result.next_allowed_at == (clock.now() + timedelta(minutes=30)).isoformat()
    assert probe.calls == ["a1", "a1"]


@pytest.mark.asyncio
async def test_a_check_with_nothing_recent_needs_no_confirmation(
    make_account: MakeAccount,
) -> None:
    await make_account("a1")
    result = await spam_intel.check_for_operator("a1", confirm_out_of_turn=False)
    assert (result.served_from, result.probed) == ("probe", True)


@pytest.mark.asyncio
async def test_a_held_check_says_why_and_when_and_did_not_probe(
    make_account: MakeAccount, probe: FakeProbe, clock: Clock
) -> None:
    await make_account("a1", proxy=None)
    result = await spam_intel.check_for_operator("a1", confirm_out_of_turn=True)
    assert (result.served_from, result.reason, result.probed) == ("refused", "no_proxy", False)
    assert probe.calls == []

    await make_account("a2")
    await spam_intel.ask("a2", force=True)
    clock.advance(minutes=5)
    within = await spam_intel.check_for_operator("a2", confirm_out_of_turn=False)
    assert (within.served_from, within.probed) == ("coalesced", False)
    assert within.next_allowed_at == (clock.now() + timedelta(minutes=25)).isoformat()
