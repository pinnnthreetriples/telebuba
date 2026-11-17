"""``verdicts_for``, the gateway's @SpamBot readings as evidence, and the lift (T12)."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING

import pytest

from core.config import settings
from core.db import fetch_warming_state, get_spam_status, upsert_spam_status, upsert_warming_state
from core.repositories.spam_evidence import list_evidence
from schemas.spam_status import SpamStatusVerdict
from schemas.warming import WarmingStateWrite
from services import spam_block_lift, spam_intel
from tests.services.spam_intel.conftest import CLEAN_TEXT, LIMITED_TEXT

if TYPE_CHECKING:
    from collections.abc import Awaitable, Callable

    from tests.services.spam_intel.conftest import Clock, FakeProbe

    MakeAccount = Callable[..., Awaitable[None]]


async def _legacy(
    account: str, status: str, at: datetime, detail: str | None = None
) -> SpamStatusVerdict:
    return await upsert_spam_status(
        SpamStatusVerdict(
            account_id=account,
            status=status,  # ty: ignore[invalid-argument-type]
            detail=detail,
            checked_at=at.isoformat(),
        )
    )


@pytest.mark.asyncio
async def test_an_account_with_nothing_stored_reads_unknown_never_clean(
    make_account: MakeAccount,
) -> None:
    await make_account("a1")
    verdicts = await spam_intel.verdicts_for(["a1", "ghost", "a1"])
    assert set(verdicts) == {"a1", "ghost"}
    assert {(v.status, v.reason) for v in verdicts.values()} == {("unknown", "no_evidence")}


@pytest.mark.asyncio
async def test_a_dated_limit_that_ended_reads_unknown_with_a_reason(
    make_account: MakeAccount, clock: Clock
) -> None:
    await make_account("a1")
    until = clock.now() + timedelta(days=2)
    await spam_intel.record_evidence(
        "a1", "spambot_limited", source="spambot", expires_at=until, detail="2026-11-19"
    )
    before = (await spam_intel.verdicts_for(["a1"]))["a1"]
    assert (before.status, before.valid_until, before.needs_probe) == ("limited", until, False)

    clock.advance(days=2, minutes=1)

    after = (await spam_intel.verdicts_for(["a1"]))["a1"]
    assert (after.status, after.reason, after.needs_probe) == ("unknown", "limit_expired", True)
    assert (after.primary_kind, after.valid_until, after.confidence) == (None, None, "low")


@pytest.mark.asyncio
async def test_a_legacy_reading_with_no_projection_reads_through_the_fold(
    make_account: MakeAccount, clock: Clock
) -> None:
    now = clock.now()
    day = (now + timedelta(days=10)).date()
    for name, status, detail in (
        ("clean", "clean", None),
        ("dated", "limited", f"until {day.isoformat()}"),
        ("undated", "limited", None),
        ("flood", "limited", "peer_flood"),
        ("unknown", "unknown", None),
        ("frozenlegacy", "unknown", "frozen"),
    ):
        await make_account(name)
        await _legacy(name, status, now - timedelta(hours=1), detail)
    got = await spam_intel.verdicts_for(
        ["clean", "dated", "undated", "flood", "unknown", "frozenlegacy"]
    )
    assert {k: (v.status, v.primary_kind) for k, v in got.items()} == {
        "clean": ("clean", "spambot_clean"),
        "dated": ("limited", "spambot_limited"),
        "undated": ("limited", "spambot_limited"),
        "flood": ("limited", "peer_flood_send"),
        "unknown": ("unknown", None),
        "frozenlegacy": ("unknown", None),
    }
    midnight = datetime(day.year, day.month, day.day, tzinfo=UTC)
    assert got["dated"].valid_until == midnight + timedelta(days=1, hours=1)
    assert got["clean"].last_spambot_at == now - timedelta(hours=1)


@pytest.mark.asyncio
async def test_a_stuck_legacy_limit_is_rechecked_and_a_dated_one_expires(
    make_account: MakeAccount, clock: Clock
) -> None:
    await make_account("old")
    await make_account("dated")
    then = clock.now() - timedelta(days=11)
    await _legacy("old", "limited", then)
    await _legacy("dated", "limited", then, f"until {(clock.now() - timedelta(days=3)).date()}")

    verdicts = await spam_intel.verdicts_for(["old", "dated"])

    old, dated = verdicts["old"], verdicts["dated"]
    assert (old.status, old.reason, old.needs_probe) == ("limited", "recheck_due", True)
    assert (dated.status, dated.reason, dated.needs_probe) == ("unknown", "limit_expired", True)


@pytest.mark.asyncio
async def test_a_legacy_write_made_after_the_fold_wins_and_keeps_the_flags(
    make_account: MakeAccount, clock: Clock
) -> None:
    await make_account("a1")
    await spam_intel.record_evidence("a1", "tos_restricted", source="session_check", detail="why")
    await spam_intel.record_evidence("a1", "dm_stranger_ok", source="outreach")
    assert (await spam_intel.verdicts_for(["a1"]))["a1"].status == "clean"

    clock.advance(minutes=5)
    await _legacy("a1", "limited", clock.now(), "peer_flood")  # masstagging's direct write

    verdict = (await spam_intel.verdicts_for(["a1"]))["a1"]
    assert (verdict.status, verdict.primary_kind) == ("limited", "peer_flood_send")
    assert (verdict.tos_restricted, verdict.tos_reason) == (True, "why")


@pytest.mark.asyncio
async def test_a_freeze_survives_a_newer_legacy_reading(
    make_account: MakeAccount, clock: Clock
) -> None:
    await make_account("a1")
    await spam_intel.record_evidence("a1", "freeze_seen", source="session_check")
    clock.advance(minutes=5)
    await _legacy("a1", "clean", clock.now())
    assert (await spam_intel.verdicts_for(["a1"]))["a1"].status == "frozen"


# The gateway: every real @SpamBot reading is evidence.


@pytest.mark.asyncio
async def test_a_dated_limited_reading_becomes_dated_evidence(
    make_account: MakeAccount, probe: FakeProbe
) -> None:
    await make_account("a1")
    probe.reply = LIMITED_TEXT
    await spam_intel.ask("a1", force=True)

    (row,) = await list_evidence("a1")
    assert (row.kind, row.source, row.operation) == ("spambot_limited", "spambot", "probe")
    # The date the reply named, plus the hold; the reply's own words are not kept.
    assert row.expires_at == datetime(2026, 10, 30, 1, tzinfo=UTC)
    assert row.detail == "2026-10-29"


@pytest.mark.asyncio
async def test_a_clean_reading_becomes_clean_evidence(
    make_account: MakeAccount, probe: FakeProbe
) -> None:
    await make_account("a1")
    probe.reply = CLEAN_TEXT
    await spam_intel.ask("a1", force=True)
    (row,) = await list_evidence("a1")
    assert (row.kind, row.expires_at, row.detail) == ("spambot_clean", None, None)
    assert (await spam_intel.verdicts_for(["a1"]))["a1"].status == "clean"


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("reply", "kind"),
    [
        ("Your account is being checked.", "spambot_reviewing"),
        ("qwerty", "spambot_unreadable"),
    ],
)
async def test_an_answer_without_a_verdict_is_kept_as_such(
    make_account: MakeAccount,
    probe: FakeProbe,
    monkeypatch: pytest.MonkeyPatch,
    reply: str,
    kind: str,
) -> None:
    await make_account("a1")
    monkeypatch.setattr(settings.spam_intel, "strict_parser", True)
    probe.reply = reply
    await spam_intel.ask("a1", force=True)
    assert [row.kind for row in await list_evidence("a1")] == [kind]


@pytest.mark.asyncio
async def test_a_failed_probe_is_not_a_reading(make_account: MakeAccount, probe: FakeProbe) -> None:
    await make_account("a1")
    probe.error = "TimeoutError"
    await spam_intel.ask("a1", force=True)
    assert await list_evidence("a1") == []


@pytest.mark.asyncio
async def test_with_signals_off_a_probe_records_no_evidence(
    make_account: MakeAccount, monkeypatch: pytest.MonkeyPatch
) -> None:
    await make_account("a1")
    monkeypatch.setattr(settings.spam_intel, "passive_signals", False)
    await spam_intel.ask("a1", force=True)
    assert await list_evidence("a1") == []
    assert (await get_spam_status("a1")) is not None  # the legacy path is unchanged


@pytest.mark.asyncio
async def test_a_reading_after_a_peer_flood_supersedes_it(
    make_account: MakeAccount, probe: FakeProbe, clock: Clock
) -> None:
    await make_account("a1")
    await spam_intel.record_evidence("a1", "peer_flood_send", source="execute")
    clock.advance(hours=1)
    probe.reply = CLEAN_TEXT
    await spam_intel.ask("a1", force=True)
    assert (await spam_intel.verdicts_for(["a1"]))["a1"].status == "clean"


# T12: only a persisted @SpamBot ``clean`` lifts a spam block; a passive clean never does.


async def _blocked(account: str, clock: Clock) -> str:
    mark = (clock.now() - timedelta(days=2)).isoformat()
    await upsert_warming_state(
        WarmingStateWrite(
            account_id=account,
            state="spam_blocked",
            started_at=(clock.now() - timedelta(days=5)).isoformat(),
            warmed_days=3,
            spam_blocked_at=mark,
            run_id="run-1",
            next_run_at=clock.now().isoformat(),
            last_error="spam blocked",
            wave=1,
        )
    )
    await _legacy(account, "limited", clock.now() - timedelta(days=2))
    return mark


@pytest.mark.asyncio
@pytest.mark.parametrize("decisions", [False, True], ids=["shadow", "passive_decisions"])
async def test_a_passive_clean_never_lifts_a_spam_block(
    make_account: MakeAccount, clock: Clock, monkeypatch: pytest.MonkeyPatch, *, decisions: bool
) -> None:
    await make_account("a1")
    mark = await _blocked("a1", clock)
    monkeypatch.setattr(settings.spam_intel, "passive_decisions", decisions)

    await spam_intel.record_evidence("a1", "peer_flood_send", source="execute")
    clock.advance(hours=1)
    await spam_intel.record_evidence("a1", "dm_stranger_ok", source="outreach")
    # The reading that named the limit (stored before evidence existed) is part of the fold: a
    # delivered first message does not supersede an @SpamBot limit, it asks for a new answer.
    verdict = (await spam_intel.verdicts_for(["a1"]))["a1"]
    assert (verdict.status, verdict.contradiction, verdict.needs_probe) == ("limited", True, True)

    assert await spam_block_lift.lift_from_stored_answer("a1") is False
    record = await fetch_warming_state("a1")
    assert record is not None
    assert (record.state, record.spam_blocked_at) == ("spam_blocked", mark)


@pytest.mark.asyncio
async def test_a_persisted_spambot_clean_still_lifts_the_block(
    make_account: MakeAccount, clock: Clock, probe: FakeProbe
) -> None:
    await make_account("a1")
    await _blocked("a1", clock)
    probe.reply = CLEAN_TEXT

    await spam_intel.ask("a1", force=True)

    record = await fetch_warming_state("a1")
    assert record is not None
    assert record.spam_blocked_at is None
