"""Evidence in the gateway's ask: served without a probe, or a probe because of it (5.2, 5.4, 5.5).

Everything here is behind ``SPAM_INTEL__PASSIVE_DECISIONS``. The first tests pin that with it OFF
the same evidence changes nothing (the Stage 1 behaviour, unmodified).
"""

from __future__ import annotations

from datetime import timedelta
from typing import TYPE_CHECKING

import pytest

from core.config import settings
from core.db import upsert_spam_status
from schemas.spam_status import SpamStatusVerdict
from services import spam_intel
from tests.services.spam_intel.conftest import LIMITED_TEXT

if TYPE_CHECKING:
    from collections.abc import Awaitable, Callable
    from datetime import datetime

    from schemas.spam_evidence import EvidenceKind
    from schemas.spam_status import SpamStatusKind
    from tests.services.spam_intel.conftest import Clock, Events, FakeProbe

    MakeAccount = Callable[..., Awaitable[None]]

_STALE_HOURS = 40.0  # past the 36 h cache TTL: the cache alone would not serve it


@pytest.fixture
def on(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(settings.spam_intel, "passive_decisions", True)


async def _legacy(account: str, status: SpamStatusKind, at: datetime, detail: str | None = None):
    await upsert_spam_status(
        SpamStatusVerdict(
            account_id=account, status=status, detail=detail, checked_at=at.isoformat()
        )
    )


async def _see(account: str, kind: EvidenceKind, at: datetime) -> None:
    await spam_intel.record_evidence(account, kind, source="spambot", observed_at=at)


async def _clean_run(account: str, clock: Clock, count: int, newest_hours_ago: float) -> None:
    """``count`` @SpamBot clean readings, 72 h apart, the newest ``newest_hours_ago`` ago."""
    for i in range(count):
        await _see(
            account, "spambot_clean", clock.now() - timedelta(hours=newest_hours_ago + 72 * i)
        )
    await _legacy(account, "clean", clock.now() - timedelta(hours=newest_hours_ago))


@pytest.mark.asyncio
async def test_off_a_confident_fold_changes_nothing_and_the_stale_cache_is_probed(
    make_account: MakeAccount, probe: FakeProbe, clock: Clock
) -> None:
    await make_account("a1")
    await _see("a1", "spambot_clean", clock.now() - timedelta(hours=_STALE_HOURS))
    await _legacy("a1", "clean", clock.now() - timedelta(hours=_STALE_HOURS))
    verdict = await spam_intel.ask("a1", purpose="routine", caller="warming.cycle")
    assert (verdict.served_from, probe.calls) == ("probe", ["a1"])


@pytest.mark.asyncio
async def test_off_an_expired_dated_limit_is_still_served_from_the_fresh_cache(
    make_account: MakeAccount, probe: FakeProbe, clock: Clock
) -> None:
    await make_account("a1")
    day = (clock.now() - timedelta(days=3)).date()
    await _legacy("a1", "limited", clock.now() - timedelta(hours=1), f"until {day.isoformat()}")
    verdict = await spam_intel.ask("a1", purpose="routine", caller="warming.cycle")
    assert (verdict.status, verdict.served_from, probe.calls) == ("limited", "cache", [])


@pytest.mark.asyncio
@pytest.mark.usefixtures("on")
async def test_on_a_confident_clean_fold_answers_a_routine_ask_without_a_probe(
    make_account: MakeAccount, probe: FakeProbe, clock: Clock, events: Events
) -> None:
    await make_account("a1")
    await _see("a1", "spambot_clean", clock.now() - timedelta(hours=_STALE_HOURS))
    await _legacy("a1", "clean", clock.now() - timedelta(hours=_STALE_HOURS))
    verdict = await spam_intel.ask("a1", purpose="routine", caller="warming.cycle")
    assert (verdict.status, verdict.served_from, probe.calls) == ("clean", "evidence", [])
    assert events.of("spam_intel_served_from_evidence") == [
        {"caller": "warming.cycle", "purpose": "routine", "status": "clean", "via": "fold"}
    ]


@pytest.mark.asyncio
@pytest.mark.usefixtures("on")
async def test_on_a_confident_limited_fold_answers_a_decision_ask_with_the_stored_detail(
    make_account: MakeAccount, probe: FakeProbe, clock: Clock
) -> None:
    await make_account("a1")
    seen = clock.now() - timedelta(hours=_STALE_HOURS)
    await _see("a1", "spambot_limited", seen)
    await _legacy("a1", "limited", seen, "undated limit text")
    verdict = await spam_intel.ask("a1", purpose="decision", caller="warming.handoff")
    assert (verdict.status, verdict.served_from, probe.calls) == ("limited", "evidence", [])
    assert verdict.detail == "undated limit text"


@pytest.mark.asyncio
@pytest.mark.usefixtures("on")
async def test_on_a_fold_with_no_stored_reading_names_its_own_kind_as_the_detail(
    make_account: MakeAccount, probe: FakeProbe, clock: Clock
) -> None:
    await make_account("a1")
    await _see("a1", "spambot_limited", clock.now() - timedelta(hours=1))
    verdict = await spam_intel.ask("a1", purpose="routine", caller="warming.cycle")
    assert (verdict.status, verdict.detail, verdict.served_from, probe.calls) == (
        "limited",
        "spambot_limited",
        "evidence",
        [],
    )


@pytest.mark.asyncio
@pytest.mark.usefixtures("on")
async def test_on_a_forced_ask_is_never_answered_from_evidence(
    make_account: MakeAccount, probe: FakeProbe, clock: Clock
) -> None:
    await make_account("a1")
    await _see("a1", "spambot_clean", clock.now() - timedelta(hours=_STALE_HOURS))
    await _legacy("a1", "clean", clock.now() - timedelta(hours=_STALE_HOURS))
    verdict = await spam_intel.ask("a1", force=True, caller="warming.quarantine")
    assert (verdict.served_from, probe.calls) == ("probe", ["a1"])


@pytest.mark.asyncio
@pytest.mark.usefixtures("on")
@pytest.mark.parametrize(
    ("purpose", "caller"),
    [("measurement", "survival.window"), ("operator", "api.operator")],
)
async def test_on_measurement_and_the_operator_still_get_a_real_answer(
    make_account: MakeAccount, probe: FakeProbe, clock: Clock, purpose: str, caller: str
) -> None:
    await make_account("a1")
    await _see("a1", "spambot_clean", clock.now() - timedelta(hours=_STALE_HOURS))
    await _legacy("a1", "clean", clock.now() - timedelta(hours=_STALE_HOURS))
    verdict = await spam_intel.ask(
        "a1",
        purpose=purpose,  # ty: ignore[invalid-argument-type]
        caller=caller,
        force=purpose == "operator",
        confirm_out_of_turn=True,
    )
    assert (verdict.served_from, probe.calls) == ("probe", ["a1"])


@pytest.mark.asyncio
@pytest.mark.usefixtures("on")
async def test_on_a_medium_confidence_fold_is_not_enough_to_skip_the_probe(
    make_account: MakeAccount, probe: FakeProbe, clock: Clock
) -> None:
    await make_account("a1")
    # PeerFlood on a send is worth 0.7: medium. It limits the verdict but does not answer for it.
    await _see("a1", "peer_flood_send", clock.now() - timedelta(hours=1))
    await _legacy("a1", "clean", clock.now() - timedelta(hours=_STALE_HOURS))
    verdict = await spam_intel.ask("a1", purpose="routine", caller="warming.cycle")
    assert (verdict.served_from, probe.calls) == ("probe", ["a1"])


@pytest.mark.asyncio
@pytest.mark.usefixtures("on")
async def test_on_a_dated_limit_that_ended_skips_the_fresh_cache_and_asks(
    make_account: MakeAccount, probe: FakeProbe, clock: Clock
) -> None:
    await make_account("a1")
    day = (clock.now() - timedelta(days=3)).date()
    detail = f"until {day.isoformat()}"
    await _legacy("a1", "limited", clock.now() - timedelta(hours=1), detail)
    verdict = await spam_intel.ask("a1", purpose="routine", caller="warming.cycle")
    assert (verdict.served_from, probe.calls) == ("probe", ["a1"])


@pytest.mark.asyncio
@pytest.mark.usefixtures("on")
async def test_on_a_contradiction_asks_a_decision_even_with_a_fresh_cache(
    make_account: MakeAccount, probe: FakeProbe, clock: Clock
) -> None:
    await make_account("a1")
    await _see("a1", "spambot_limited", clock.now() - timedelta(hours=5))
    await _see("a1", "dm_stranger_ok", clock.now() - timedelta(hours=1))
    await _legacy("a1", "limited", clock.now() - timedelta(hours=5), LIMITED_TEXT)
    verdict = await spam_intel.ask("a1", purpose="decision", caller="neurocomment.ban_confirm")
    assert (verdict.served_from, probe.calls) == ("probe", ["a1"])


@pytest.mark.asyncio
@pytest.mark.usefixtures("on")
async def test_on_a_needs_probe_ask_still_meets_the_budget(
    make_account: MakeAccount, probe: FakeProbe, clock: Clock
) -> None:
    await make_account("a1", proxy=None)  # no proxy, never asked
    await _see("a1", "spambot_limited", clock.now() - timedelta(hours=5))
    await _see("a1", "dm_stranger_ok", clock.now() - timedelta(hours=1))
    await _legacy("a1", "limited", clock.now() - timedelta(hours=5), LIMITED_TEXT)
    verdict = await spam_intel.ask("a1", purpose="decision", caller="neurocomment.ban_confirm")
    assert (verdict.served_from, verdict.reason, probe.calls) == ("refused", "no_proxy", [])


@pytest.mark.asyncio
@pytest.mark.usefixtures("on")
async def test_on_a_long_clean_run_stretches_the_routine_interval_to_120_hours(
    make_account: MakeAccount, probe: FakeProbe, clock: Clock, events: Events
) -> None:
    await make_account("a1")
    await _clean_run("a1", clock, count=3, newest_hours_ago=100.0)  # 72 h is over, 120 h is not
    verdict = await spam_intel.ask("a1", purpose="routine", caller="warming.cycle")
    assert (verdict.status, verdict.served_from, probe.calls) == ("clean", "cache", [])
    assert events.of("spam_intel_served_from_evidence")[0]["via"] == "interval"

    clock.advance(hours=21)  # 121 h since the last reading
    verdict = await spam_intel.ask("a1", purpose="routine", caller="warming.cycle")
    assert (verdict.served_from, probe.calls) == ("probe", ["a1"])


@pytest.mark.asyncio
@pytest.mark.usefixtures("on")
async def test_on_a_longer_clean_run_stretches_it_to_168_hours(
    make_account: MakeAccount, probe: FakeProbe, clock: Clock
) -> None:
    await make_account("a1")
    await _clean_run("a1", clock, count=5, newest_hours_ago=150.0)
    verdict = await spam_intel.ask("a1", purpose="routine", caller="warming.cycle")
    assert (verdict.served_from, probe.calls) == ("cache", [])
    clock.advance(hours=19)  # 169 h
    verdict = await spam_intel.ask("a1", purpose="routine", caller="warming.cycle")
    assert (verdict.served_from, probe.calls) == ("probe", ["a1"])


@pytest.mark.asyncio
@pytest.mark.usefixtures("on")
async def test_on_a_short_clean_run_keeps_warmings_own_interval(
    make_account: MakeAccount, probe: FakeProbe, clock: Clock
) -> None:
    await make_account("a1")
    await _clean_run("a1", clock, count=2, newest_hours_ago=100.0)
    verdict = await spam_intel.ask("a1", purpose="routine", caller="warming.cycle")
    assert (verdict.served_from, probe.calls) == ("probe", ["a1"])


@pytest.mark.asyncio
@pytest.mark.usefixtures("on")
async def test_on_limiting_evidence_resets_the_stretch(
    make_account: MakeAccount, clock: Clock, events: Events
) -> None:
    await make_account("a1")
    await _clean_run("a1", clock, count=5, newest_hours_ago=100.0)
    await _see("a1", "peer_flood_other", clock.now() - timedelta(hours=10))
    verdict = await spam_intel.ask("a1", purpose="routine", caller="warming.cycle")
    # The clean record no longer answers for the account: what is known now is the limit
    # (written through to the stored status while the fold decides), not a stretched clean.
    assert verdict.status == "limited"
    assert [e["via"] for e in events.of("spam_intel_served_from_evidence")] == []


@pytest.mark.asyncio
async def test_off_a_long_clean_run_does_not_stretch_anything(
    make_account: MakeAccount, probe: FakeProbe, clock: Clock
) -> None:
    await make_account("a1")
    await _clean_run("a1", clock, count=5, newest_hours_ago=100.0)
    verdict = await spam_intel.ask("a1", purpose="routine", caller="warming.cycle")
    assert (verdict.served_from, probe.calls) == ("probe", ["a1"])


@pytest.mark.asyncio
@pytest.mark.usefixtures("on")
async def test_on_a_decision_ask_is_not_stretched_by_the_routine_interval(
    make_account: MakeAccount, probe: FakeProbe, clock: Clock
) -> None:
    await make_account("a1")
    await _clean_run("a1", clock, count=5, newest_hours_ago=100.0)
    verdict = await spam_intel.ask("a1", purpose="decision", caller="warming.handoff")
    assert (verdict.served_from, probe.calls) == ("probe", ["a1"])
