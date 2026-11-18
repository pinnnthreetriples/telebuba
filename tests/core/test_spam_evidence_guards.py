"""What the evidence repository refuses or protects (audit F-007, F-016, F-018, shadow quota).

* a projection older than the stored one is not written (F-007);
* an observation of a deleted account, or of an account created after the signal was raised, is
  not inserted, and deleting an account cancels its signals still in flight (F-016);
* a repeat counts in SQL, so two racing repeats both count (F-018);
* shadow rows have their own quota and the live @SpamBot limit is never pruned (audit gap 4).
"""

from __future__ import annotations

import asyncio
import threading
from datetime import UTC, datetime, timedelta
from typing import Any

import pytest

from core.db import create_account, delete_account
from core.repositories import spam_evidence
from core.repositories.spam_evidence import list_evidence, prune_evidence, record_evidence
from core.repositories.spam_status import list_projections_by_ids, store_projection
from core.spam_signals import drain_signals, emit_signal, set_signal_sink
from schemas.accounts import AccountCreate
from schemas.spam_evidence import Evidence, EvidenceKind, Verdict
from services.spam_intel._evidence_policy import KIND_POLICY, SHADOW_KINDS

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


# --- F-007: the projection never goes back in time ---------------------------------------------


@pytest.mark.asyncio
async def test_an_older_fold_does_not_replace_a_newer_one() -> None:
    await create_account(AccountCreate(account_id="a1"))

    assert await store_projection("a1", Verdict(status="frozen"), NOW + timedelta(minutes=1))
    assert not await store_projection("a1", Verdict(status="unknown"), NOW)

    row = (await list_projections_by_ids(["a1"]))["a1"]
    assert row.verdict is not None
    assert row.verdict.status == "frozen"
    # A later (or the same) instant does replace it.
    assert await store_projection("a1", Verdict(status="clean"), NOW + timedelta(minutes=2))
    row = (await list_projections_by_ids(["a1"]))["a1"]
    assert row.verdict is not None
    assert row.verdict.status == "clean"


@pytest.mark.asyncio
async def test_a_projection_for_an_account_that_is_gone_is_not_written() -> None:
    assert not await store_projection("ghost", Verdict(status="frozen"), NOW)
    assert (await list_projections_by_ids(["ghost"])) == {}


# --- F-016: a late signal and a deleted (or re-registered) account -----------------------------


@pytest.mark.asyncio
async def test_an_observation_of_a_deleted_account_is_not_stored() -> None:
    await create_account(AccountCreate(account_id="a1"))
    await delete_account("a1")

    stored = await record_evidence(_item(), window=WINDOW, require_account=True)

    assert stored is False
    assert await list_evidence("a1") == []


@pytest.mark.asyncio
async def test_a_signal_raised_before_the_account_was_registered_again_is_refused() -> None:
    raised = datetime.now(UTC)
    await asyncio.sleep(0.01)
    await create_account(AccountCreate(account_id="a1"))  # same id, created after the signal

    assert not await record_evidence(
        _item("freeze_seen", at=raised), window=WINDOW, require_account=True, emitted_at=raised
    )
    assert await list_evidence("a1") == []
    # A signal raised after the account existed is stored.
    assert await record_evidence(
        _item("freeze_seen"), window=WINDOW, require_account=True, emitted_at=datetime.now(UTC)
    )


@pytest.mark.asyncio
async def test_deleting_an_account_cancels_its_signals_still_in_flight() -> None:
    await create_account(AccountCreate(account_id="a1"))
    delivered: list[str] = []

    async def sink(account_id: str, kind: str, **_kwargs: object) -> None:
        await asyncio.sleep(0.05)
        delivered.append(f"{account_id}:{kind}")

    set_signal_sink(sink)
    emit_signal("a1", "freeze_seen", source="execute")
    emit_signal("a2", "freeze_seen", source="execute")

    await delete_account("a1")
    await drain_signals()

    assert delivered == ["a2:freeze_seen"]  # the other account's signal still arrives


# --- F-018: the counter is atomic --------------------------------------------------------------


@pytest.mark.asyncio
async def test_two_repeats_racing_each_other_both_count(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    await record_evidence(_item(at=NOW), window=WINDOW)
    barrier = threading.Barrier(2, timeout=5)
    real: Any = spam_evidence._coalesces

    def _both_read_first(*args: object, **kwargs: object) -> bool:
        decided = real(*args, **kwargs)
        barrier.wait()  # each repeat has read the row before either has written it
        return decided

    monkeypatch.setattr(spam_evidence, "_coalesces", _both_read_first)
    await asyncio.gather(
        record_evidence(_item(at=NOW + timedelta(minutes=1)), window=WINDOW),
        record_evidence(_item(at=NOW + timedelta(minutes=2)), window=WINDOW),
    )

    (row,) = await list_evidence("a1")
    assert row.count == 3


@pytest.mark.asyncio
async def test_a_repeat_reports_a_change_only_when_the_fold_has_something_new() -> None:
    assert await record_evidence(_item(at=NOW, detail="a"), window=WINDOW) is True
    same = _item(at=NOW + timedelta(minutes=1), detail="a")
    assert await record_evidence(same, window=WINDOW) is False
    other = _item(at=NOW + timedelta(minutes=2), detail="b")
    assert await record_evidence(other, window=WINDOW) is True


# --- shadow quota and the live limit -----------------------------------------------------------


async def _day_of_shadows(day: int, *, account: str = "a1") -> None:
    at = NOW + timedelta(days=day)
    for kind in sorted(SHADOW_KINDS)[:3]:
        await record_evidence(_item(kind, account=account, at=at, source="execute"), window=WINDOW)


@pytest.mark.asyncio
async def test_three_shadow_signals_a_day_never_evict_a_live_spambot_limit() -> None:
    await record_evidence(_item("spambot_limited", at=NOW, source="spambot"), window=WINDOW)
    for day in range(1, 26):
        await _day_of_shadows(day)
        await prune_evidence(
            NOW - timedelta(days=30),
            50,
            now=NOW + timedelta(days=day),
            shadow_kinds=SHADOW_KINDS,
            shadow_max_per_account=16,
        )

    rows = await list_evidence("a1")
    kinds = [row.kind for row in rows]
    assert "spambot_limited" in kinds
    assert sum(1 for kind in kinds if kind in SHADOW_KINDS) == 16  # its own small quota


@pytest.mark.asyncio
async def test_shadow_rows_do_not_use_up_the_cap_of_real_evidence() -> None:
    for minute in range(6):
        await record_evidence(
            _item("peer_flood_send", at=NOW + timedelta(minutes=minute)), window=timedelta(0)
        )
    for minute in range(20):
        await record_evidence(
            _item("join_ok", at=NOW + timedelta(minutes=30 + minute)), window=timedelta(0)
        )

    await prune_evidence(
        NOW - timedelta(days=30),
        6,
        now=NOW,
        shadow_kinds=SHADOW_KINDS,
        shadow_max_per_account=4,
    )

    kinds = [row.kind for row in await list_evidence("a1")]
    assert kinds.count("peer_flood_send") == 6  # none traded for a shadow sighting
    assert kinds.count("join_ok") == 4


@pytest.mark.asyncio
async def test_the_cap_never_prunes_the_unexpired_limit_that_decides_the_account() -> None:
    await record_evidence(_item("spambot_limited", at=NOW, source="spambot"), window=WINDOW)
    for minute in range(1, 12):
        await record_evidence(
            _item("flood_wait", at=NOW + timedelta(minutes=minute * 20)), window=timedelta(0)
        )

    await prune_evidence(NOW - timedelta(days=30), 5, now=NOW + timedelta(hours=5))

    kinds = [row.kind for row in await list_evidence("a1")]
    assert "spambot_limited" in kinds  # undated: it still decides
    assert kinds.count("flood_wait") == 5


@pytest.mark.asyncio
async def test_a_limit_whose_date_has_passed_is_pruned_like_any_other_row() -> None:
    ended = NOW + timedelta(hours=1)
    await record_evidence(
        _item("spambot_limited", at=NOW, source="spambot", expires_at=ended), window=WINDOW
    )
    for minute in range(1, 8):
        await record_evidence(
            _item("flood_wait", at=NOW + timedelta(hours=2, minutes=minute * 20)),
            window=timedelta(0),
        )

    await prune_evidence(NOW - timedelta(days=30), 5, now=NOW + timedelta(days=1))

    kinds = [row.kind for row in await list_evidence("a1")]
    assert "spambot_limited" not in kinds
