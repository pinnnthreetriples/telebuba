"""The cycle's snapshot (item 5.1): what it answers, how long it lives, what the settle re-reads."""

from __future__ import annotations

from dataclasses import replace
from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING

import pytest

from core.db import create_account, load_warming_settings, upsert_warming_state
from core.repositories.accounts import update_account_status
from core.repositories.spam_status import get_spam_status, upsert_spam_status
from core.repositories.warming import fetch_warming_state
from core.repositories.warming_daily_stats import list_warming_daily_stats
from core.repositories.warming_floods import record_warming_flood_event
from schemas._warming_extras import JoinedChannel
from schemas.accounts import AccountCreate
from schemas.spam_status import SpamStatusVerdict
from schemas.warming import WarmingStateWrite
from schemas.warming_flood import WarmingFloodEventWrite
from services import warming
from services.trust import account_trust_score
from services.warming import _seams
from services.warming._snapshot import (
    _CycleSnapshot,
    current_snapshot,
    load_cycle_snapshot,
    publish_snapshot,
    refresh_after_cycle,
    snapshot_scope,
)
from services.warming.pacing import _account_tz
from tests.services.warming._support import _Recorder, _seed_ready_account, _set_settings

if TYPE_CHECKING:
    from schemas.telegram_actions import ActionResult, TelegramAction


async def _snapshot(account_id: str = "acc-1") -> _CycleSnapshot:
    return await load_cycle_snapshot(account_id, await load_warming_settings(), None, None)


def _joined(channel: str, created_at: str, left_at: str | None = None) -> JoinedChannel:
    return JoinedChannel(channel=channel, created_at=created_at, left_at=left_at)


@pytest.mark.asyncio
async def test_joined_rows_answer_like_the_queries_they_replace() -> None:
    now = datetime.now(UTC)
    recent = (now - timedelta(hours=2)).isoformat()
    old = (now - timedelta(hours=30)).isoformat()
    snapshot = replace(
        await _snapshot(),
        joined=(_joined("a", recent), _joined("b", old), _joined("c", recent, recent)),
    )

    assert snapshot.is_joined("a")
    assert snapshot.is_joined("b")
    # Left counts as not joined, but its join still sits inside the rolling day.
    assert not snapshot.is_joined("c")
    assert not snapshot.is_joined("d")
    assert snapshot.joins_since(now - timedelta(hours=24)) == 2


@pytest.mark.asyncio
async def test_an_unknown_account_scores_critical() -> None:
    snapshot = await _snapshot("ghost")

    trust = snapshot.trust(datetime.now(UTC))

    assert (trust.score, trust.band, trust.reasons) == (0, "critical", ["unknown account"])


@pytest.mark.asyncio
async def test_the_snapshot_trust_matches_the_full_query() -> None:
    await _seed_ready_account("acc-1")
    await upsert_spam_status(
        SpamStatusVerdict(
            account_id="acc-1", status="limited", checked_at=datetime.now(UTC).isoformat()
        )
    )
    await upsert_warming_state(
        WarmingStateWrite(account_id="acc-1", state="active", quarantine_count=1)
    )

    snapshot = await load_cycle_snapshot(
        "acc-1",
        await load_warming_settings(),
        await fetch_warming_state("acc-1"),
        await get_spam_status("acc-1"),
    )

    assert snapshot.trust(datetime.now(UTC)) == await account_trust_score("acc-1")


@pytest.mark.asyncio
async def test_the_timezone_comes_from_the_snapshot_only_inside_its_scope() -> None:
    await create_account(AccountCreate(account_id="acc-1"))
    snapshot = await _snapshot()
    assert snapshot.account is not None
    # A Mexican number in the snapshot only: the stored account has no phone.
    stand_in = replace(
        snapshot, account=snapshot.account.model_copy(update={"phone": "+525512345678"})
    )

    with snapshot_scope():
        publish_snapshot(stand_in)
        inside = await _account_tz("acc-1")
        other = current_snapshot("acc-2")

    assert inside == stand_in.tz
    assert inside is not None
    assert other is None
    assert current_snapshot("acc-1") is None
    assert await _account_tz("acc-1") is None


@pytest.mark.asyncio
async def test_the_settle_re_reads_the_account_and_after_a_peer_flood_the_floods() -> None:
    await create_account(AccountCreate(account_id="acc-1"))
    with snapshot_scope():
        publish_snapshot(await _snapshot())
        await update_account_status("acc-1", status="frozen")
        await record_warming_flood_event(
            WarmingFloodEventWrite(
                account_id="acc-1", created_at=datetime.now(UTC).isoformat(), action="send_dm"
            )
        )

        calm = await refresh_after_cycle("acc-1", flooded=False)
        flooded = await refresh_after_cycle("acc-1", flooded=True)

    assert calm is not None
    assert flooded is not None
    assert calm.account is not None
    assert calm.account.status == "frozen"
    assert calm.floods is not None
    assert calm.floods.flood_total == 0
    assert flooded.floods is not None
    assert flooded.floods.flood_total == 1
    assert await refresh_after_cycle("acc-1", flooded=True) is None


@pytest.mark.asyncio
async def test_a_session_dying_mid_cycle_reaches_the_days_trust(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    recorder = _Recorder()

    async def execute(account_id: str, action: TelegramAction) -> ActionResult:
        if action.action_type == "read_channel":
            # What the typed-action executor writes when Telegram revokes the session.
            await update_account_status(account_id, status="unauthorized")
        return await recorder.execute(account_id, action)

    monkeypatch.setattr(_seams, "execute", execute)
    await _seed_ready_account("acc-1")
    await _set_settings(chat=False, reactions=False, key="", enforce_readiness=False)
    await upsert_warming_state(WarmingStateWrite(account_id="acc-1", state="sleeping"))

    await warming.run_loop_iteration("acc-1")

    [row] = await list_warming_daily_stats("acc-1", "2000-01-01")
    after = await account_trust_score("acc-1")
    assert "status unauthorized" in after.reasons
    assert (row.trust_score, row.trust_band) == (after.score, after.band)
