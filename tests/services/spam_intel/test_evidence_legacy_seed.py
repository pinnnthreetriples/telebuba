"""The reading stored before the evidence layer is part of the fold until a real one is recorded."""

from __future__ import annotations

from datetime import timedelta
from typing import TYPE_CHECKING

import pytest

from core.config import settings
from core.db import upsert_spam_status
from schemas.spam_status import SpamStatusVerdict
from services import spam_intel
from tests.services.spam_intel.conftest import CLEAN_TEXT

if TYPE_CHECKING:
    from collections.abc import Awaitable, Callable

    from schemas.spam_status import SpamStatusKind
    from tests.services.spam_intel.conftest import Clock, Events, FakeProbe

    MakeAccount = Callable[..., Awaitable[None]]


async def _legacy(account: str, status: SpamStatusKind, ago_hours: float, clock: Clock) -> None:
    await upsert_spam_status(
        SpamStatusVerdict(
            account_id=account,
            status=status,
            detail=None,
            checked_at=(clock.now() - timedelta(hours=ago_hours)).isoformat(),
        )
    )


@pytest.mark.asyncio
async def test_a_delivered_first_message_does_not_supersede_an_older_spambot_limit(
    make_account: MakeAccount, clock: Clock
) -> None:
    await make_account("a1")
    await _legacy("a1", "limited", 48.0, clock)  # stored before evidence existed

    await spam_intel.record_evidence("a1", "dm_stranger_ok", source="outreach")

    verdict = (await spam_intel.verdicts_for(["a1"]))["a1"]
    assert (verdict.status, verdict.primary_kind) == ("limited", "spambot_limited")
    assert (verdict.contradiction, verdict.needs_probe) == (True, True)


@pytest.mark.asyncio
async def test_the_first_evidence_on_an_already_limited_account_is_not_a_change(
    make_account: MakeAccount, clock: Clock, events: Events
) -> None:
    await make_account("a1")
    await _legacy("a1", "limited", 48.0, clock)

    await spam_intel.record_evidence("a1", "peer_flood_send", source="execute")

    # Same status and the same deciding kind as the stored reading: nothing for the bot to say.
    assert events.of("spam_intel_verdict_changed") == []


@pytest.mark.asyncio
async def test_the_first_evidence_on_a_clean_account_reports_the_change_from_clean(
    make_account: MakeAccount, clock: Clock, events: Events
) -> None:
    await make_account("a1")
    await _legacy("a1", "clean", 5.0, clock)

    await spam_intel.record_evidence("a1", "peer_flood_send", source="execute")

    (changed,) = events.of("spam_intel_verdict_changed")
    assert (changed["from"], changed["to"], changed["primary_kind"]) == (
        "clean",
        "limited",
        "peer_flood_send",
    )


@pytest.mark.asyncio
async def test_the_first_flag_on_a_clean_account_does_not_forget_the_clean_reading(
    make_account: MakeAccount, clock: Clock
) -> None:
    await make_account("a1")
    await _legacy("a1", "clean", 5.0, clock)

    await spam_intel.record_evidence("a1", "tos_restricted", source="session_check", detail="x")

    verdict = (await spam_intel.verdicts_for(["a1"]))["a1"]
    assert (verdict.status, verdict.tos_restricted) == ("clean", True)


@pytest.mark.asyncio
async def test_a_real_reading_recorded_later_replaces_the_stored_one(
    make_account: MakeAccount, clock: Clock, probe: FakeProbe
) -> None:
    await make_account("a1")
    await _legacy("a1", "limited", 48.0, clock)
    await spam_intel.record_evidence("a1", "peer_flood_other", source="read")
    probe.reply = CLEAN_TEXT

    await spam_intel.ask("a1", force=True, caller="warming.quarantine")

    verdict = (await spam_intel.verdicts_for(["a1"]))["a1"]
    assert (verdict.status, verdict.primary_kind) == ("clean", "spambot_clean")


@pytest.mark.asyncio
async def test_a_limit_written_through_by_passive_decisions_is_not_read_back_as_spambots(
    make_account: MakeAccount, monkeypatch: pytest.MonkeyPatch, clock: Clock
) -> None:
    monkeypatch.setattr(settings.spam_intel, "passive_decisions", True)
    await make_account("a1")
    await spam_intel.record_evidence("a1", "peer_flood_send", source="execute")
    clock.advance(minutes=30)
    await spam_intel.record_evidence("a1", "user_banned_in_channel", source="execute")

    verdict = (await spam_intel.verdicts_for(["a1"]))["a1"]

    # The mirrored ``limited`` row is the fold's own word, not a 1.0-weight @SpamBot reading.
    assert (verdict.status, verdict.primary_kind) == ("limited", "peer_flood_send")
    assert verdict.confidence == "medium"


@pytest.mark.asyncio
async def test_a_stored_unreadable_reading_leaves_the_fold_unknown(
    make_account: MakeAccount, clock: Clock
) -> None:
    await make_account("a1")
    await _legacy("a1", "unknown", 3.0, clock)

    await spam_intel.record_evidence("a1", "flood_wait", source="execute")

    verdict = (await spam_intel.verdicts_for(["a1"]))["a1"]
    assert verdict.status == "unknown"
