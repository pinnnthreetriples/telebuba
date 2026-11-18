"""Evidence and the account's life: a late signal, a re-registered id, an ended limit.

* F-016: a signal still in flight when its account is deleted never writes evidence, and a new
  account that reuses the id does not inherit it (no ``frozen`` out of nowhere).
* F-023: the verdict-change event compares with the verdict AS OF NOW: a new limit after the
  previous one ended is a change from ``unknown``, not a repeat of ``limited``.
"""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING

import pytest

from core import spam_signals
from core.db import delete_account
from core.repositories.spam_evidence import list_evidence
from core.spam_signals import drain_signals, emit_signal
from services import spam_intel

if TYPE_CHECKING:
    from collections.abc import Awaitable, Callable

    from tests.services.spam_intel.conftest import Clock, Events

    MakeAccount = Callable[..., Awaitable[None]]


@pytest.mark.asyncio
async def test_a_late_signal_does_not_outlive_its_account_nor_haunt_a_new_one(
    make_account: MakeAccount,
) -> None:
    await make_account("a1")
    spam_intel.install_signal_sink()
    emit_signal("a1", "freeze_seen", source="session_check")  # raised, not yet delivered

    await delete_account("a1")
    await drain_signals()
    assert await list_evidence("a1") == []

    await make_account("a1")  # the same id registered again
    await spam_intel.record_evidence("a1", "join_ok", source="execute")
    verdict = (await spam_intel.verdicts_for(["a1"]))["a1"]
    assert verdict.status != "frozen"
    assert [item.kind for item in await list_evidence("a1")] == ["join_ok"]


@pytest.mark.asyncio
async def test_a_signal_that_got_past_the_cancel_is_refused_by_the_insert(
    make_account: MakeAccount,
) -> None:
    """The task was already inside its write when the account went (it cannot be cancelled)."""
    raised = datetime.now(UTC)
    await asyncio.sleep(0.01)
    await make_account("a1")  # created AFTER the signal was raised: another account, same id

    token = spam_signals._EMITTED_AT.set(raised)
    try:
        await spam_intel.record_evidence("a1", "freeze_seen", source="session_check")
    finally:
        spam_signals._EMITTED_AT.reset(token)

    assert await list_evidence("a1") == []
    assert (await spam_intel.verdicts_for(["a1"]))["a1"].status == "unknown"


@pytest.mark.asyncio
async def test_a_new_limit_after_the_previous_one_ended_is_a_change_from_unknown(
    make_account: MakeAccount, clock: Clock, events: Events
) -> None:
    await make_account("a1")
    await spam_intel.record_evidence(
        "a1",
        "spambot_limited",
        source="spambot",
        observed_at=clock.now(),
        expires_at=clock.now() + timedelta(days=1),
    )
    clock.advance(days=3)  # the dated limit ran out; nothing folded since

    await spam_intel.record_evidence("a1", "peer_flood_send", source="execute")

    changed = events.of("spam_intel_verdict_changed")[-1]
    assert (changed["from"], changed["to"], changed["primary_kind"]) == (
        "unknown",
        "limited",
        "peer_flood_send",
    )


@pytest.mark.asyncio
async def test_three_shadow_signals_a_day_for_weeks_never_evict_the_limit_that_decides(
    make_account: MakeAccount, clock: Clock
) -> None:
    """Audit gap 4: on day 17 the 51st row used to push a live @SpamBot limit out of the cap."""
    await make_account("a1")
    await spam_intel.record_evidence("a1", "spambot_limited", source="spambot")
    for _day in range(25):
        clock.advance(days=1)
        for kind in ("group_send_ok", "join_ok", "read_ok"):
            await spam_intel.record_evidence("a1", kind, source="execute")

    kinds = [item.kind for item in await list_evidence("a1")]
    assert "spambot_limited" in kinds
    verdict = (await spam_intel.verdicts_for(["a1"]))["a1"]
    assert (verdict.status, verdict.primary_kind) == ("limited", "spambot_limited")
