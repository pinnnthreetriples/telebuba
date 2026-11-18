"""The stored answer and the folded projection stay apart (audit F-006, F-007, F-015, F-018).

* F-006: ``passive_decisions`` ON then OFF leaves the legacy ``status`` / ``detail`` /
  ``checked_at`` exactly as @SpamBot said; a late fold can never write over a newer stored answer.
* F-007: a fold written late never replaces a newer one (a freeze is not undone by a stale
  ``unknown``).
* F-015: a reply that says nothing ("being checked") does not erase a still-valid dated limit.
* F-018: a repeat that brings a new end date or reason moves the projection too.
"""

from __future__ import annotations

import asyncio
from datetime import timedelta
from typing import TYPE_CHECKING

import pytest

from core.config import settings
from core.db import get_spam_status, upsert_spam_status
from core.repositories.spam_evidence import list_evidence
from core.repositories.spam_status import list_projections_by_ids
from schemas.spam_status import SpamStatusVerdict
from services import spam_intel
from services.spam_intel import _decisions, _evidence
from tests.services.spam_intel._reply_fixtures import FIXTURES

if TYPE_CHECKING:
    from collections.abc import Awaitable, Callable
    from datetime import datetime

    from schemas.spam_evidence import Verdict
    from schemas.spam_status import SpamStatusKind
    from tests.services.spam_intel.conftest import Clock, FakeProbe

    MakeAccount = Callable[..., Awaitable[None]]


async def _legacy(
    account: str, status: SpamStatusKind, at: datetime, detail: str | None = None
) -> SpamStatusVerdict:
    return await upsert_spam_status(
        SpamStatusVerdict(
            account_id=account, status=status, detail=detail, checked_at=at.isoformat()
        )
    )


def _after_the_stored_reading(clock: Clock) -> None:
    """Put the suite's clock past the executor's own (real) stamp, as production's always is.

    The executor stamps a reading with the real time it started; the fold's ``folded_at`` comes
    from the (here frozen) evidence clock, and a reading stamped AFTER the last fold reads as
    written behind the evidence layer's back. In production the fold is always the later one.
    """
    clock.advance(minutes=1)


async def _projection(account: str = "a1") -> Verdict | None:
    row = (await list_projections_by_ids([account])).get(account)
    return None if row is None else row.verdict


# --- F-006 ------------------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_passive_decisions_on_then_off_leaves_the_stored_answer_as_the_bot_gave_it(
    make_account: MakeAccount, clock: Clock, monkeypatch: pytest.MonkeyPatch
) -> None:
    await make_account("a1")
    said = await _legacy("a1", "clean", clock.now() - timedelta(hours=1))
    monkeypatch.setattr(settings.spam_intel, "passive_decisions", True)

    await spam_intel.record_evidence("a1", "peer_flood_send", source="execute")
    await spam_intel.record_evidence("a1", "freeze_seen", source="session_check")

    # ON: the reader's decision is the fold's, applied at read time ...
    on_view = await _decisions.decision_status("a1", await get_spam_status("a1"))
    assert on_view is not None
    assert (on_view.status, on_view.detail) == ("limited", "frozen")
    # ... and nothing was written into the legacy columns.
    assert await get_spam_status("a1") == said

    monkeypatch.setattr(settings.spam_intel, "passive_decisions", False)
    off_view = await _decisions.decision_status("a1", await get_spam_status("a1"))
    assert off_view == said  # the rollback reads what @SpamBot said, not what the fold decided
    projection = await _projection()
    assert projection is not None
    assert projection.status == "frozen"  # the decision stays in the projection columns


@pytest.mark.asyncio
async def test_a_fold_that_lands_after_a_newer_stored_clean_does_not_overwrite_it(
    make_account: MakeAccount, clock: Clock, monkeypatch: pytest.MonkeyPatch
) -> None:
    await make_account("a1")
    monkeypatch.setattr(settings.spam_intel, "passive_decisions", True)
    real_store = _evidence.store_projection
    answer: dict[str, SpamStatusVerdict] = {}

    async def _delayed(*args: object, **kwargs: object) -> bool:
        # The probe's clean lands while this fold is on its way to the table.
        answer["clean"] = await _legacy("a1", "clean", clock.now() + timedelta(seconds=1))
        return await real_store(*args, **kwargs)  # ty: ignore[invalid-argument-type]

    monkeypatch.setattr(_evidence, "store_projection", _delayed)

    await spam_intel.record_evidence("a1", "peer_flood_send", source="execute")

    assert await get_spam_status("a1") == answer["clean"]


# --- F-007 ------------------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_a_delayed_stale_fold_does_not_replace_a_newer_frozen_one(
    make_account: MakeAccount, monkeypatch: pytest.MonkeyPatch, clock: Clock
) -> None:
    await make_account("a1")
    real_store = _evidence.store_projection
    held = asyncio.Event()
    calls: list[str] = []

    async def _gated(account_id: str, verdict: Verdict, folded_at: datetime) -> bool:
        calls.append(verdict.status)
        if len(calls) == 1:  # the first fold (join_ok -> unknown) is held up on its way
            await held.wait()
        return await real_store(account_id, verdict, folded_at)

    monkeypatch.setattr(_evidence, "store_projection", _gated)
    slow = asyncio.create_task(spam_intel.record_evidence("a1", "join_ok", source="execute"))
    await asyncio.sleep(0.05)
    clock.advance(seconds=5)
    fast = asyncio.create_task(spam_intel.record_evidence("a1", "freeze_seen", source="execute"))
    await asyncio.sleep(0.2)
    held.set()
    await asyncio.gather(slow, fast)

    projection = await _projection()
    assert projection is not None
    assert projection.status == "frozen"
    assert (await spam_intel.verdicts_for(["a1"]))["a1"].status == "frozen"


@pytest.mark.asyncio
async def test_folds_of_one_account_are_serialised(
    make_account: MakeAccount, monkeypatch: pytest.MonkeyPatch
) -> None:
    await make_account("a1")
    real_store = _evidence.store_projection
    running = 0
    peak = 0

    async def _counting(*args: object, **kwargs: object) -> bool:
        nonlocal running, peak
        running += 1
        peak = max(peak, running)
        await asyncio.sleep(0.02)
        running -= 1
        return await real_store(*args, **kwargs)  # ty: ignore[invalid-argument-type]

    monkeypatch.setattr(_evidence, "store_projection", _counting)
    await asyncio.gather(
        spam_intel.record_evidence("a1", "join_ok", source="execute"),
        spam_intel.record_evidence("a1", "peer_flood_send", source="execute"),
        spam_intel.record_evidence("a1", "freeze_seen", source="session_check"),
    )

    assert peak == 1
    projection = await _projection()
    assert projection is not None
    assert projection.status == "frozen"


# --- F-015 ------------------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_the_first_being_checked_reply_keeps_an_unexpired_dated_limit(
    make_account: MakeAccount, clock: Clock, probe: FakeProbe
) -> None:
    await make_account("a1")
    until = (clock.now() + timedelta(days=5)).date()
    await _legacy("a1", "limited", clock.now() - timedelta(hours=60), f"until {until.isoformat()}")
    probe.reply = FIXTURES["en"].being_checked
    _after_the_stored_reading(clock)

    await spam_intel.ask("a1", force=True, caller="warming.quarantine")

    stored = await get_spam_status("a1")
    assert stored is not None
    assert (stored.status, stored.detail) == ("unknown", "account is being checked")
    verdict = (await spam_intel.verdicts_for(["a1"]))["a1"]
    assert (verdict.status, verdict.primary_kind) == ("limited", "spambot_limited")
    assert verdict.valid_until is not None
    assert verdict.valid_until.date() == until + timedelta(days=1)  # the day, plus the hold


@pytest.mark.asyncio
async def test_an_expired_limit_is_not_kept_alive_by_a_being_checked_reply(
    make_account: MakeAccount, clock: Clock, probe: FakeProbe
) -> None:
    await make_account("a1")
    until = (clock.now() - timedelta(days=3)).date()
    await _legacy("a1", "limited", clock.now() - timedelta(days=8), f"until {until.isoformat()}")
    probe.reply = FIXTURES["en"].being_checked
    _after_the_stored_reading(clock)

    await spam_intel.ask("a1", force=True, caller="warming.quarantine")

    verdict = (await spam_intel.verdicts_for(["a1"]))["a1"]
    assert (verdict.status, verdict.reason) == ("unknown", "limit_expired")


@pytest.mark.asyncio
async def test_the_prior_reading_is_recorded_once_across_several_replies(
    make_account: MakeAccount, clock: Clock, probe: FakeProbe
) -> None:
    await make_account("a1")
    await _legacy("a1", "limited", clock.now() - timedelta(hours=60))  # undated
    probe.reply = FIXTURES["en"].being_checked
    _after_the_stored_reading(clock)

    await spam_intel.ask("a1", force=True, caller="warming.quarantine")
    clock.advance(hours=7)
    await spam_intel.ask("a1", force=True, caller="warming.quarantine")

    kinds = sorted(item.kind for item in await list_evidence("a1"))
    assert kinds.count("spambot_limited") == 1
    verdict = (await spam_intel.verdicts_for(["a1"]))["a1"]
    assert verdict.status == "limited"


# --- F-018 ------------------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_a_repeat_with_a_later_end_date_moves_the_projection(
    make_account: MakeAccount, clock: Clock
) -> None:
    await make_account("a1")
    first = clock.now() + timedelta(days=1)
    await spam_intel.record_evidence(
        "a1", "spambot_limited", source="spambot", observed_at=clock.now(), expires_at=first
    )
    later = clock.now() + timedelta(days=9)
    clock.advance(minutes=2)
    await spam_intel.record_evidence(
        "a1", "spambot_limited", source="spambot", observed_at=clock.now(), expires_at=later
    )

    assert len(await list_evidence("a1")) == 1  # one row, a count of two
    verdict = (await spam_intel.verdicts_for(["a1"]))["a1"]
    assert verdict.valid_until == later


@pytest.mark.asyncio
async def test_a_repeat_with_a_fresher_restriction_reason_moves_the_projection(
    make_account: MakeAccount, clock: Clock
) -> None:
    await make_account("a1")
    await spam_intel.record_evidence("a1", "tos_restricted", source="session_check", detail="old")
    clock.advance(minutes=2)
    await spam_intel.record_evidence("a1", "tos_restricted", source="session_check", detail="new")

    verdict = (await spam_intel.verdicts_for(["a1"]))["a1"]
    assert (verdict.tos_restricted, verdict.tos_reason) == (True, "new")


@pytest.mark.asyncio
async def test_a_plain_repeat_does_not_fold_again(
    make_account: MakeAccount, clock: Clock, monkeypatch: pytest.MonkeyPatch
) -> None:
    await make_account("a1")
    await spam_intel.record_evidence("a1", "peer_flood_send", source="execute", detail="X")
    stored: list[object] = []
    real_store = _evidence.store_projection

    async def _spy(*args: object, **kwargs: object) -> bool:
        stored.append(args)
        return await real_store(*args, **kwargs)  # ty: ignore[invalid-argument-type]

    monkeypatch.setattr(_evidence, "store_projection", _spy)
    clock.advance(minutes=1)
    await spam_intel.record_evidence("a1", "peer_flood_send", source="execute", detail="X")

    assert stored == []
    (row,) = await list_evidence("a1")
    assert row.count == 2
