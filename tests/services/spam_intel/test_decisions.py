"""The decision view of the stored status: off = untouched, on = the fold decides (section 6)."""

from __future__ import annotations

from datetime import timedelta
from typing import TYPE_CHECKING

import pytest

from core.config import settings
from core.db import upsert_spam_status
from core.repositories.spam_status import list_spam_statuses_by_ids
from schemas.spam_status import SpamStatusVerdict
from services import spam_intel
from services.spam_intel import _evidence

if TYPE_CHECKING:
    from collections.abc import Awaitable, Callable
    from datetime import datetime

    from schemas.spam_evidence import EvidenceKind
    from schemas.spam_status import SpamStatusKind
    from tests.services.spam_intel.conftest import Clock

    MakeAccount = Callable[..., Awaitable[None]]


@pytest.fixture
def on(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(settings.spam_intel, "passive_decisions", True)


def _switch_on(monkeypatch: pytest.MonkeyPatch) -> None:
    """Evidence is recorded in shadow first, so the fold (not a mirrored write) is what answers."""
    monkeypatch.setattr(settings.spam_intel, "passive_decisions", True)


async def _legacy(
    account: str, status: SpamStatusKind, at: datetime, detail: str | None = None
) -> None:
    await upsert_spam_status(
        SpamStatusVerdict(
            account_id=account, status=status, detail=detail, checked_at=at.isoformat()
        )
    )


async def _see(account: str, kind: EvidenceKind, at: datetime, source: str = "execute") -> None:
    await spam_intel.record_evidence(account, kind, source=source, observed_at=at)


async def _views(*accounts: str) -> dict[str, SpamStatusVerdict]:
    stored = await list_spam_statuses_by_ids(list(accounts))
    return await spam_intel.decision_statuses(accounts, stored)


@pytest.mark.asyncio
async def test_off_the_stored_rows_come_back_untouched_and_nothing_is_read(
    make_account: MakeAccount, clock: Clock, monkeypatch: pytest.MonkeyPatch
) -> None:
    await make_account("a1")
    day = (clock.now() - timedelta(days=3)).date()
    await _legacy("a1", "limited", clock.now(), f"until {day.isoformat()}")
    await _see("a1", "freeze_seen", clock.now(), "session_check")

    async def _never(_ids: object) -> None:
        pytest.fail("the evidence layer was read while passive_decisions is off")

    monkeypatch.setattr(_evidence, "verdicts_for", _never)
    stored = await list_spam_statuses_by_ids(["a1"])
    assert await spam_intel.decision_statuses(["a1"], stored) == stored
    assert await spam_intel.decision_status("a1", stored["a1"]) == stored["a1"]
    assert await spam_intel.decision_status("a1", None) is None


@pytest.mark.asyncio
async def test_the_master_switch_off_also_leaves_the_rows_untouched(
    make_account: MakeAccount, clock: Clock, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(settings.spam_intel, "passive_decisions", True)
    monkeypatch.setattr(settings.spam_intel, "enabled", False)
    await make_account("a1")
    await _legacy("a1", "limited", clock.now(), f"until {(clock.now() - timedelta(days=3)).date()}")
    stored = await list_spam_statuses_by_ids(["a1"])
    assert await spam_intel.decision_statuses(["a1"], stored) == stored


@pytest.mark.asyncio
@pytest.mark.usefixtures("on")
async def test_on_a_dated_limit_that_ended_reads_unknown_not_limited(
    make_account: MakeAccount, clock: Clock
) -> None:
    await make_account("a1")
    day = (clock.now() - timedelta(days=3)).date()
    await _legacy("a1", "limited", clock.now() - timedelta(days=4), f"until {day.isoformat()}")
    view = (await _views("a1"))["a1"]
    assert (view.status, view.detail) == ("unknown", "limit_expired")
    assert view.checked_at == (clock.now() - timedelta(days=4)).isoformat()


@pytest.mark.asyncio
@pytest.mark.usefixtures("on")
async def test_on_a_limit_still_running_and_an_undated_one_stay_limited_with_their_detail(
    make_account: MakeAccount, clock: Clock
) -> None:
    await make_account("dated")
    await make_account("undated")
    day = (clock.now() + timedelta(days=3)).date()
    await _legacy("dated", "limited", clock.now(), f"until {day.isoformat()}")
    await _legacy("undated", "limited", clock.now() - timedelta(days=11), "no date given")
    views = await _views("dated", "undated")
    # An undated limit is not lifted by time: it stays limited (and is rechecked elsewhere).
    assert (views["dated"].status, views["dated"].detail) == ("limited", f"until {day.isoformat()}")
    assert (views["undated"].status, views["undated"].detail) == ("limited", "no date given")


@pytest.mark.asyncio
async def test_on_fresh_peer_flood_evidence_blocks_an_account_with_no_stored_reading(
    make_account: MakeAccount, clock: Clock, monkeypatch: pytest.MonkeyPatch
) -> None:
    await make_account("a1")
    await _see("a1", "peer_flood_send", clock.now() - timedelta(hours=1))
    assert await list_spam_statuses_by_ids(["a1"]) == {}  # nothing stored by the legacy path...
    _switch_on(monkeypatch)
    view = (await _views("a1"))["a1"]
    assert (view.status, view.detail) == ("limited", "peer_flood_send")


@pytest.mark.asyncio
async def test_on_peer_flood_after_a_clean_reading_reads_limited(
    make_account: MakeAccount, clock: Clock, monkeypatch: pytest.MonkeyPatch
) -> None:
    await make_account("a1")
    await _legacy("a1", "clean", clock.now() - timedelta(hours=5))
    await _see("a1", "peer_flood_send", clock.now() - timedelta(hours=1))
    _switch_on(monkeypatch)
    view = (await _views("a1"))["a1"]
    assert (view.status, view.detail) == ("limited", "peer_flood_send")


@pytest.mark.asyncio
async def test_on_a_freeze_blocks_and_says_frozen(
    make_account: MakeAccount, clock: Clock, monkeypatch: pytest.MonkeyPatch
) -> None:
    await make_account("a1")
    await _legacy("a1", "clean", clock.now() - timedelta(hours=5))
    await _see("a1", "freeze_seen", clock.now(), "session_check")
    _switch_on(monkeypatch)
    view = (await _views("a1"))["a1"]
    assert (view.status, view.detail) == ("limited", "frozen")


@pytest.mark.asyncio
async def test_on_a_passive_clean_never_promotes_unknown_to_clean(
    make_account: MakeAccount, clock: Clock, monkeypatch: pytest.MonkeyPatch
) -> None:
    await make_account("a1")
    await _legacy("a1", "unknown", clock.now() - timedelta(hours=5))
    await _see("a1", "dm_stranger_ok", clock.now() - timedelta(hours=1), "outreach")
    _switch_on(monkeypatch)
    view = (await _views("a1"))["a1"]
    assert view.status == "unknown"


@pytest.mark.asyncio
async def test_on_a_passive_clean_never_lifts_a_limit(
    make_account: MakeAccount, clock: Clock, monkeypatch: pytest.MonkeyPatch
) -> None:
    """D4: a delivered first message supersedes PeerFlood in the fold, yet the status stays."""
    await make_account("a1")
    await _legacy("a1", "limited", clock.now() - timedelta(hours=5), "peer_flood")
    await _see("a1", "dm_stranger_ok", clock.now() - timedelta(hours=1), "outreach")
    folded = (await spam_intel.verdicts_for(["a1"]))["a1"]
    assert folded.status == "clean"  # the fold would say so
    _switch_on(monkeypatch)
    view = (await _views("a1"))["a1"]
    assert (view.status, view.detail) == ("limited", "peer_flood")  # the decision does not


@pytest.mark.asyncio
async def test_on_a_platform_restriction_never_moves_the_status(
    make_account: MakeAccount, clock: Clock, monkeypatch: pytest.MonkeyPatch
) -> None:
    await make_account("a1")
    await _legacy("a1", "clean", clock.now() - timedelta(hours=5))
    await spam_intel.record_evidence(
        "a1", "tos_restricted", source="session_check", detail="platform", observed_at=clock.now()
    )
    verdict = (await spam_intel.verdicts_for(["a1"]))["a1"]
    assert (verdict.status, verdict.tos_restricted, verdict.tos_reason) == (
        "clean",
        True,
        "platform",
    )
    _switch_on(monkeypatch)
    view = (await _views("a1"))["a1"]
    assert view.status == "clean"


@pytest.mark.asyncio
@pytest.mark.usefixtures("on")
async def test_on_an_account_the_fold_has_no_opinion_on_stays_absent_like_before(
    make_account: MakeAccount,
) -> None:
    await make_account("a1")
    assert await _views("a1", "ghost") == {}


@pytest.mark.asyncio
@pytest.mark.usefixtures("on")
async def test_on_a_confirmed_clean_stays_clean_and_keeps_its_timestamp(
    make_account: MakeAccount, clock: Clock
) -> None:
    await make_account("a1")
    at = clock.now() - timedelta(hours=2)
    await _legacy("a1", "clean", at)
    view = (await _views("a1"))["a1"]
    assert (view.status, view.checked_at) == ("clean", at.isoformat())
