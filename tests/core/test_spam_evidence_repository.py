"""Spam evidence repository: coalescing, retention, and the projection beside the legacy reading."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from core.db import create_account, get_spam_status, list_spam_statuses, upsert_spam_status
from core.repositories.spam_evidence import (
    delete_evidence,
    list_evidence,
    prune_evidence,
    record_evidence,
)
from core.repositories.spam_status import (
    list_projections_by_ids,
    list_spam_statuses_by_ids,
    store_projection,
)
from schemas.accounts import AccountCreate
from schemas.spam_evidence import Evidence, EvidenceKind, Verdict
from schemas.spam_status import SpamStatusVerdict
from services.spam_intel._evidence_policy import KIND_POLICY

NOW = datetime(2026, 11, 17, 12, 0, tzinfo=UTC)
WINDOW = timedelta(minutes=10)


def _item(
    kind: EvidenceKind = "peer_flood_send",
    *,
    account: str = "a1",
    at: datetime = NOW,
    source: str = "execute",
    **fields: object,
) -> Evidence:
    return Evidence.model_validate(
        {
            "account_id": account,
            "kind": kind,
            "direction": KIND_POLICY[kind].direction,
            "observed_at": at,
            "source": source,
            **fields,
        }
    )


@pytest.mark.asyncio
async def test_a_repeat_inside_the_window_is_one_row_with_a_count() -> None:
    assert await record_evidence(_item(at=NOW), window=WINDOW) is True
    assert await record_evidence(_item(at=NOW + timedelta(minutes=9)), window=WINDOW) is False
    assert (
        await record_evidence(_item(at=NOW + timedelta(minutes=9, seconds=30)), window=WINDOW)
        is False
    )

    (row,) = await list_evidence("a1")
    assert (row.count, row.observed_at, row.last_seen_at) == (
        3,
        NOW,
        NOW + timedelta(minutes=9, seconds=30),
    )


@pytest.mark.asyncio
async def test_the_latest_sighting_brings_its_own_date_and_detail() -> None:
    await record_evidence(_item(at=NOW, detail="old"), window=WINDOW)
    later = NOW + timedelta(minutes=1)
    await record_evidence(
        _item(at=later, detail="new", expires_at=later + timedelta(hours=1)), window=WINDOW
    )
    (row,) = await list_evidence("a1")
    assert (row.detail, row.expires_at) == ("new", later + timedelta(hours=1))


@pytest.mark.asyncio
async def test_after_the_window_a_new_row_starts() -> None:
    await record_evidence(_item(at=NOW), window=WINDOW)
    assert await record_evidence(_item(at=NOW + timedelta(minutes=10)), window=WINDOW) is True
    assert [row.count for row in await list_evidence("a1")] == [1, 1]


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "other",
    [{"kind": "peer_flood_other"}, {"source": "chat"}, {"account": "a2"}],
    ids=["another kind", "another source", "another account"],
)
async def test_only_the_same_account_kind_and_source_coalesce(other: dict[str, str]) -> None:
    await record_evidence(_item(), window=WINDOW)
    assert await record_evidence(_item(**other), window=WINDOW) is True  # ty: ignore[invalid-argument-type]


@pytest.mark.asyncio
async def test_a_state_kind_coalesces_across_any_span_until_its_opposite_arrives() -> None:
    day = timedelta(days=1)
    await record_evidence(_item("freeze_seen", at=NOW), window=WINDOW)
    assert await record_evidence(_item("freeze_seen", at=NOW + 3 * day), window=WINDOW) is False
    assert await record_evidence(_item("freeze_cleared", at=NOW + 4 * day), window=WINDOW) is True
    # The freeze came back after its closer: that is a new fact, not a repeat.
    assert await record_evidence(_item("freeze_seen", at=NOW + 5 * day), window=WINDOW) is True
    kinds = [(row.kind, row.count) for row in await list_evidence("a1")]
    assert kinds == [("freeze_seen", 1), ("freeze_cleared", 1), ("freeze_seen", 2)]


@pytest.mark.asyncio
async def test_listing_is_newest_first_and_bounded() -> None:
    for hours in (0, 3, 1):
        await record_evidence(_item(at=NOW + timedelta(hours=hours)), window=timedelta(0))
    ordered = [row.observed_at for row in await list_evidence("a1")]
    assert ordered == sorted(ordered, reverse=True)
    assert len(await list_evidence("a1", limit=2)) == 2
    assert await list_evidence("nobody") == []


@pytest.mark.asyncio
async def test_prune_drops_what_is_older_than_the_retention() -> None:
    for days in (40, 31, 29, 1):
        await record_evidence(_item(at=NOW - timedelta(days=days)), window=timedelta(0))
    removed = await prune_evidence(NOW - timedelta(days=30), 50)
    assert removed == 2
    assert [(NOW - row.observed_at).days for row in await list_evidence("a1")] == [1, 29]


@pytest.mark.asyncio
async def test_prune_keeps_the_newest_rows_per_account() -> None:
    for minutes in range(8):
        await record_evidence(_item(at=NOW - timedelta(minutes=minutes)), window=timedelta(0))
        await record_evidence(
            _item(account="a2", at=NOW - timedelta(minutes=minutes)), window=timedelta(0)
        )
    await prune_evidence(NOW - timedelta(days=30), 5, account_id="a1")
    assert len(await list_evidence("a1")) == 5
    assert len(await list_evidence("a2")) == 8  # the cap was asked for one account only
    newest = [row.observed_at for row in await list_evidence("a1")]
    assert min(newest) == NOW - timedelta(minutes=4)
    await prune_evidence(NOW - timedelta(days=30), 5)
    assert len(await list_evidence("a2")) == 5


@pytest.mark.asyncio
async def test_prune_never_drops_the_newest_of_a_state_kind() -> None:
    old = NOW - timedelta(days=90)
    await record_evidence(_item("freeze_seen", at=old), window=WINDOW)
    await record_evidence(_item("tos_restricted", at=old), window=WINDOW)
    for minutes in range(10):
        await record_evidence(_item(at=NOW - timedelta(minutes=minutes)), window=timedelta(0))
    await prune_evidence(NOW - timedelta(days=30), 3)
    kinds = sorted(row.kind for row in await list_evidence("a1"))
    assert kinds == [
        "freeze_seen",
        "peer_flood_send",
        "peer_flood_send",
        "peer_flood_send",
        "tos_restricted",
    ]


@pytest.mark.asyncio
async def test_delete_removes_one_accounts_rows() -> None:
    await record_evidence(_item(), window=WINDOW)
    await record_evidence(_item(account="a2"), window=WINDOW)
    assert await delete_evidence("a1") == 1
    assert await list_evidence("a1") == []
    assert len(await list_evidence("a2")) == 1


# The projection lives beside the legacy reading and must not disturb its readers.


def _folded(**fields: object) -> Verdict:
    return Verdict.model_validate({"status": "limited", "confidence": "high", **fields})


@pytest.mark.asyncio
async def test_a_projection_alone_is_invisible_to_the_legacy_readers() -> None:
    await create_account(AccountCreate(account_id="a1"))
    await store_projection("a1", _folded(primary_kind="peer_flood_send"), NOW)

    assert await get_spam_status("a1") is None
    assert await list_spam_statuses() == {}
    assert await list_spam_statuses_by_ids(["a1"]) == {}
    row = (await list_projections_by_ids(["a1"]))["a1"]
    assert row.legacy is None
    assert row.verdict is not None
    assert (row.verdict.status, row.verdict.primary_kind, row.folded_at) == (
        "limited",
        "peer_flood_send",
        NOW,
    )


@pytest.mark.asyncio
async def test_storing_a_projection_leaves_the_legacy_reading_alone() -> None:
    await create_account(AccountCreate(account_id="a1"))
    legacy = SpamStatusVerdict(
        account_id="a1", status="clean", detail="d", checked_at="2026-11-01T00:00:00+00:00"
    )
    await upsert_spam_status(legacy)

    await store_projection("a1", _folded(), NOW)

    assert await get_spam_status("a1") == legacy
    row = (await list_projections_by_ids(["a1"]))["a1"]
    assert row.legacy == legacy


@pytest.mark.asyncio
async def test_a_legacy_write_after_a_projection_keeps_the_projection() -> None:
    await create_account(AccountCreate(account_id="a1"))
    await store_projection("a1", _folded(reason="contradiction", contradiction=True), NOW)
    legacy = SpamStatusVerdict(
        account_id="a1", status="limited", checked_at="2026-11-17T13:00:00+00:00"
    )

    await upsert_spam_status(legacy)

    assert await get_spam_status("a1") == legacy
    verdict = (await list_projections_by_ids(["a1"]))["a1"].verdict
    assert verdict is not None
    assert (verdict.contradiction, verdict.reason) == (True, "contradiction")


@pytest.mark.asyncio
async def test_the_projection_round_trips_every_field() -> None:
    await create_account(AccountCreate(account_id="a1"))
    verdict = _folded(
        valid_until=NOW + timedelta(days=2),
        primary_kind="spambot_limited",
        last_spambot_at=NOW - timedelta(hours=3),
        needs_probe=True,
        reason="recheck_due",
        tos_restricted=True,
        tos_reason="platform",
    )
    await store_projection("a1", verdict, NOW)
    again = await list_projections_by_ids(["a1", "missing"])
    assert set(again) == {"a1"}
    assert again["a1"].verdict == verdict
