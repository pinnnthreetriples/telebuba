"""``record_evidence``: cheap, switchable, never raises; the fold is a shadow by default."""

from __future__ import annotations

import logging
from datetime import timedelta
from typing import TYPE_CHECKING

import pytest

from core.config import settings
from core.db import get_spam_status, upsert_spam_status
from core.repositories.spam_evidence import list_evidence
from core.repositories.spam_status import list_projections_by_ids
from schemas.spam_status import SpamStatusVerdict
from services import spam_intel
from services.spam_intel import _evidence

if TYPE_CHECKING:
    from collections.abc import Awaitable, Callable

    from tests.services.spam_intel.conftest import Clock, Events

    MakeAccount = Callable[..., Awaitable[None]]


async def _projection(account_id: str = "a1") -> object:
    row = (await list_projections_by_ids([account_id])).get(account_id)
    return None if row is None else row.verdict


@pytest.mark.asyncio
async def test_a_signal_is_stored_folded_and_logged(
    make_account: MakeAccount, events: Events, clock: Clock
) -> None:
    await make_account("a1")
    await spam_intel.record_evidence(
        "a1", "peer_flood_send", source="execute", operation="send_dm", detail="PeerFloodError"
    )

    (row,) = await list_evidence("a1")
    assert (row.kind, row.direction, row.source, row.operation, row.detail) == (
        "peer_flood_send",
        "limited",
        "execute",
        "send_dm",
        "PeerFloodError",
    )
    assert row.observed_at == clock.now()
    verdict = (await spam_intel.verdicts_for(["a1"]))["a1"]
    assert (verdict.status, verdict.primary_kind) == ("limited", "peer_flood_send")
    assert events.of("spam_intel_evidence") == [
        {"kind": "peer_flood_send", "direction": "limited", "source": "execute"}
    ]
    (changed,) = events.of("spam_intel_verdict_changed")
    assert (changed["from"], changed["to"], changed["shadow"]) == (None, "limited", True)


@pytest.mark.asyncio
async def test_the_fold_is_a_shadow_the_legacy_status_is_not_touched(
    make_account: MakeAccount, clock: Clock
) -> None:
    await make_account("a1")
    reading = SpamStatusVerdict(
        account_id="a1", status="clean", detail=None, checked_at=clock.now().isoformat()
    )
    await upsert_spam_status(reading)

    await spam_intel.record_evidence("a1", "peer_flood_send", source="execute")
    await spam_intel.record_evidence("a1", "freeze_seen", source="session_check")

    assert await get_spam_status("a1") == reading
    assert (await spam_intel.verdicts_for(["a1"]))["a1"].status == "frozen"


@pytest.mark.asyncio
async def test_an_account_with_no_reading_gets_no_legacy_row(make_account: MakeAccount) -> None:
    await make_account("a1")
    await spam_intel.record_evidence("a1", "peer_flood_send", source="execute")
    assert await get_spam_status("a1") is None
    assert await _projection() is not None


@pytest.mark.asyncio
async def test_a_burst_is_one_row_one_event_and_one_fold(
    make_account: MakeAccount, events: Events, clock: Clock
) -> None:
    await make_account("a1")
    for _ in range(3):
        await spam_intel.record_evidence("a1", "peer_flood_send", source="execute")
        clock.advance(minutes=2)

    (row,) = await list_evidence("a1")
    assert row.count == 3
    assert len(events.of("spam_intel_evidence")) == 1
    assert len(events.of("spam_intel_verdict_changed")) == 1
    clock.advance(minutes=20)
    await spam_intel.record_evidence("a1", "peer_flood_send", source="execute")
    assert len(await list_evidence("a1")) == 2


@pytest.mark.asyncio
async def test_a_verdict_that_did_not_change_is_not_logged_again(
    make_account: MakeAccount, events: Events, clock: Clock
) -> None:
    await make_account("a1")
    await spam_intel.record_evidence("a1", "peer_flood_send", source="execute")
    clock.advance(hours=1)
    await spam_intel.record_evidence("a1", "user_banned_in_channel", source="chat")
    clock.advance(hours=1)
    await spam_intel.record_evidence("a1", "dm_stranger_ok", source="outreach")
    changes = [(e["from"], e["to"]) for e in events.of("spam_intel_verdict_changed")]
    # Limited by PeerFlood; a weaker limit changes nothing; a delivered first message: clean.
    assert changes == [(None, "limited"), ("limited", "clean")]


@pytest.mark.asyncio
async def test_the_kinds_own_time_to_live_is_applied(make_account: MakeAccount) -> None:
    await make_account("a1")
    await spam_intel.record_evidence("a1", "spambot_reviewing", source="spambot")
    (row,) = await list_evidence("a1")
    assert row.expires_at == row.observed_at + timedelta(hours=6)


@pytest.mark.asyncio
async def test_detail_and_source_are_bounded(make_account: MakeAccount) -> None:
    await make_account("a1")
    await spam_intel.record_evidence("a1", "peer_flood_other", source="s" * 300, detail="d" * 900)
    (row,) = await list_evidence("a1")
    assert (len(row.detail or ""), len(row.source)) == (200, 64)


@pytest.mark.asyncio
@pytest.mark.parametrize("switch", ["passive_signals", "enabled"])
async def test_a_switch_off_records_nothing(
    make_account: MakeAccount, monkeypatch: pytest.MonkeyPatch, events: Events, switch: str
) -> None:
    await make_account("a1")
    monkeypatch.setattr(settings.spam_intel, switch, False)
    await spam_intel.record_evidence("a1", "peer_flood_send", source="execute")
    assert await list_evidence("a1") == []
    assert await _projection() is None
    assert events.rows == []


@pytest.mark.asyncio
async def test_it_never_raises_into_the_caller(
    make_account: MakeAccount, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    await make_account("a1")

    async def _broken(*_args: object, **_kwargs: object) -> bool:
        msg = "secret-token-in-the-message"
        raise RuntimeError(msg)

    monkeypatch.setattr(_evidence, "store_evidence", _broken)
    with caplog.at_level(logging.WARNING):
        await spam_intel.record_evidence("a1", "peer_flood_send", source="execute")
    assert "RuntimeError" in caplog.text
    assert "secret-token" not in caplog.text


@pytest.mark.asyncio
async def test_a_bad_kind_or_a_missing_account_does_not_raise(make_account: MakeAccount) -> None:
    await make_account("a1")
    await spam_intel.record_evidence("a1", "not_a_kind", source="execute")  # ty: ignore[invalid-argument-type]
    await spam_intel.record_evidence("a1", "peer_flood_send", source="")
    # No such account: the evidence is kept, the projection cannot be (a foreign key), no raise.
    await spam_intel.record_evidence("ghost", "peer_flood_send", source="execute")
    assert await list_evidence("a1") == []


@pytest.mark.asyncio
async def test_a_failing_log_sink_does_not_raise(
    make_account: MakeAccount, monkeypatch: pytest.MonkeyPatch
) -> None:
    await make_account("a1")

    async def _boom(*_args: object, **_kwargs: object) -> None:
        raise OSError

    monkeypatch.setattr(_evidence, "log_event", _boom)
    await spam_intel.record_evidence("a1", "peer_flood_send", source="execute")
    assert len(await list_evidence("a1")) == 1


@pytest.mark.asyncio
async def test_retention_keeps_the_newest_rows(
    make_account: MakeAccount, monkeypatch: pytest.MonkeyPatch, clock: Clock
) -> None:
    await make_account("a1")
    monkeypatch.setattr(settings.spam_intel, "evidence_max_per_account", 5)
    monkeypatch.setattr(settings.spam_intel, "evidence_coalesce_minutes", 0.0)
    for _ in range(8):
        await spam_intel.record_evidence("a1", "peer_flood_other", source="read")
        clock.advance(minutes=1)
    assert len(await list_evidence("a1")) == 5
    clock.advance(days=31)
    await spam_intel.record_evidence("a1", "join_ok", source="join")
    assert [row.kind for row in await list_evidence("a1")] == ["join_ok"]


# ``passive_decisions``: a non-clean fold is written into the legacy status; a clean never is.


@pytest.fixture
def decisions(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(settings.spam_intel, "passive_decisions", True)


@pytest.mark.asyncio
@pytest.mark.usefixtures("decisions")
async def test_passive_decisions_write_a_limit_and_a_freeze(
    make_account: MakeAccount, clock: Clock, events: Events
) -> None:
    await make_account("a1")
    await upsert_spam_status(
        SpamStatusVerdict(account_id="a1", status="clean", checked_at=clock.now().isoformat())
    )
    clock.advance(hours=1)
    await spam_intel.record_evidence("a1", "peer_flood_send", source="execute")
    stored = await get_spam_status("a1")
    assert stored is not None
    assert (stored.status, stored.detail, stored.checked_at) == (
        "limited",
        "peer_flood_send",
        clock.now().isoformat(),
    )
    assert events.of("spam_intel_verdict_changed")[-1]["shadow"] is False

    clock.advance(hours=1)
    await spam_intel.record_evidence("a1", "freeze_seen", source="session_check")
    stored = await get_spam_status("a1")
    assert stored is not None
    assert (stored.status, stored.detail) == ("unknown", "frozen")


@pytest.mark.asyncio
@pytest.mark.usefixtures("decisions")
async def test_a_passive_clean_never_writes_the_legacy_status(
    make_account: MakeAccount, clock: Clock
) -> None:
    await make_account("a1")
    await spam_intel.record_evidence("a1", "peer_flood_send", source="execute")
    clock.advance(hours=1)
    await spam_intel.record_evidence("a1", "dm_stranger_ok", source="outreach")

    assert (await spam_intel.verdicts_for(["a1"]))["a1"].status == "clean"
    stored = await get_spam_status("a1")
    assert stored is not None
    assert stored.status == "limited"  # still what the limit wrote: only @SpamBot says clean


@pytest.mark.asyncio
@pytest.mark.usefixtures("decisions")
async def test_an_unchanged_legacy_verdict_is_not_rewritten(
    make_account: MakeAccount, clock: Clock
) -> None:
    await make_account("a1")
    await spam_intel.record_evidence("a1", "peer_flood_send", source="execute")
    first = await get_spam_status("a1")
    clock.advance(hours=1)
    await spam_intel.record_evidence("a1", "user_banned_in_channel", source="chat")
    assert await get_spam_status("a1") == first  # still limited: no new timestamp


@pytest.mark.asyncio
@pytest.mark.usefixtures("decisions")
async def test_a_real_answer_that_decides_is_left_to_the_probe(
    make_account: MakeAccount, clock: Clock
) -> None:
    await make_account("a1")
    probe_row = SpamStatusVerdict(
        account_id="a1",
        status="limited",
        detail="until 29 Oct 2026",
        checked_at=clock.now().isoformat(),
    )
    await upsert_spam_status(probe_row)
    await spam_intel.record_evidence("a1", "spambot_limited", source="spambot")
    assert await get_spam_status("a1") == probe_row
