"""Tests for ``services.dialogues`` — acquaintance-pair assignment."""

from __future__ import annotations

import asyncio
from collections import Counter
from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING

import pytest

from core.config import settings
from core.db import (
    configure_database,
    create_account,
    latest_unreplied_for,
    list_dialogue_pairs,
    record_dialogue_message,
    replace_dialogue_pairs,
    try_claim_message_reply,
    update_account_from_session_check,
    upsert_warming_state,
)
from core.logging import reset_logging_for_tests, setup_logging
from schemas.accounts import AccountCreate, AccountStatus
from schemas.dialogues import DialoguePair
from schemas.telegram_session import TelegramSessionCheckResult
from schemas.warming import WarmingStateWrite
from services.dialogues import _build_pairs, _time_to_reshuffle, assign_pairs, get_partners

if TYPE_CHECKING:
    from collections.abc import Iterator
    from pathlib import Path


@pytest.fixture(autouse=True)
def _isolate(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    configure_database(tmp_path / "telebuba.db")
    monkeypatch.setattr(settings.telegram, "session_dir", tmp_path / "sessions")
    monkeypatch.setattr(settings.logging, "path", tmp_path / "debug.log")
    monkeypatch.setattr(settings.logging, "sentry_dsn", "")
    reset_logging_for_tests()
    setup_logging()
    yield
    reset_logging_for_tests()


async def _seed_warming(account_id: str, status: AccountStatus = "new") -> None:
    await create_account(AccountCreate(account_id=account_id))
    if status != "new":
        await update_account_from_session_check(
            TelegramSessionCheckResult(
                account_id=account_id,
                session_path=account_id,
                status=status,
                is_temporary=False,
            ),
        )
    await upsert_warming_state(WarmingStateWrite(account_id=account_id, state="active"))


@pytest.mark.asyncio
async def test_assign_pairs_needs_two_accounts() -> None:
    await _seed_warming("acc-1")
    assert (await assign_pairs()).pairs == []


@pytest.mark.asyncio
async def test_assign_pairs_creates_mutual_partners() -> None:
    for i in (1, 2, 3):
        await _seed_warming(f"acc-{i}")

    result = await assign_pairs()

    assert result.pairs
    partners = (await get_partners("acc-1")).partners
    assert partners
    assert set(partners) <= {"acc-2", "acc-3"}
    # pairing is symmetric
    assert "acc-1" in (await get_partners(partners[0])).partners


@pytest.mark.asyncio
async def test_assign_pairs_excludes_dead_accounts() -> None:
    await _seed_warming("acc-1")
    await _seed_warming("acc-2")
    await _seed_warming("acc-dead", status="account_error")

    await assign_pairs()

    assert (await get_partners("acc-dead")).partners == []
    assert set((await get_partners("acc-1")).partners) <= {"acc-2"}


@pytest.mark.asyncio
async def test_assign_pairs_is_stable_without_change() -> None:
    for i in (1, 2, 3):
        await _seed_warming(f"acc-{i}")

    first = (await assign_pairs()).pairs
    again = (await assign_pairs()).pairs

    def _key(pairs: list) -> set[tuple[str, str]]:
        return {(pair.account_a, pair.account_b) for pair in pairs}

    assert _key(first) == _key(again)
    assert first[0].assigned_at == again[0].assigned_at


@pytest.mark.asyncio
async def test_assign_pairs_weaves_in_new_account_without_reshuffle() -> None:
    await _seed_warming("acc-1")
    await _seed_warming("acc-2")
    await assign_pairs()
    original_at = (await list_dialogue_pairs())[0].assigned_at

    await _seed_warming("acc-3")
    pairs = (await assign_pairs()).pairs

    covered = {pair.account_a for pair in pairs} | {pair.account_b for pair in pairs}
    assert covered == {"acc-1", "acc-2", "acc-3"}
    # The pre-existing acc-1/acc-2 acquaintance is patched around, not rebuilt:
    # its timestamp is untouched, proving no full reshuffle fired.
    kept = next(p for p in pairs if {p.account_a, p.account_b} == {"acc-1", "acc-2"})
    assert kept.assigned_at == original_at


@pytest.mark.asyncio
async def test_assign_pairs_prunes_dropped_account_and_keeps_survivors() -> None:
    for i in (1, 2, 3):
        await _seed_warming(f"acc-{i}")
    # Pin a known graph so the assertion doesn't depend on the random build.
    await replace_dialogue_pairs([("acc-1", "acc-2"), ("acc-2", "acc-3")])
    survivor_at = next(p.assigned_at for p in await list_dialogue_pairs() if p.account_a == "acc-1")

    # acc-3 freezes → only its pair should be pruned; acc-1/acc-2 stays intact.
    await update_account_from_session_check(
        TelegramSessionCheckResult(
            account_id="acc-3",
            session_path="acc-3",
            status="account_error",
            is_temporary=False,
        ),
    )
    pairs = (await assign_pairs()).pairs

    assert [(p.account_a, p.account_b) for p in pairs] == [("acc-1", "acc-2")]
    assert pairs[0].assigned_at == survivor_at


def test_time_to_reshuffle_time_based() -> None:
    now = datetime(2026, 1, 20, tzinfo=UTC)
    stale_at = (now - timedelta(days=settings.warming.dialogue_reshuffle_days + 1)).isoformat()
    fresh_at = (now - timedelta(days=1)).isoformat()

    stale = [DialoguePair(account_a="a", account_b="b", assigned_at=stale_at)]
    fresh = [DialoguePair(account_a="a", account_b="b", assigned_at=fresh_at)]

    assert _time_to_reshuffle(stale, now) is True
    assert _time_to_reshuffle(fresh, now) is False


def test_time_to_reshuffle_uses_oldest_pair() -> None:
    now = datetime(2026, 1, 20, tzinfo=UTC)
    stale_at = (now - timedelta(days=settings.warming.dialogue_reshuffle_days + 1)).isoformat()
    fresh_at = (now - timedelta(days=1)).isoformat()
    # A freshly-patched pair must not reset the clock started by the older pair.
    pairs = [
        DialoguePair(account_a="a", account_b="b", assigned_at=stale_at),
        DialoguePair(account_a="a", account_b="c", assigned_at=fresh_at),
    ]

    assert _time_to_reshuffle(pairs, now) is True


def test_time_to_reshuffle_malformed_assigned_at() -> None:
    now = datetime(2026, 1, 20, tzinfo=UTC)
    pairs = [DialoguePair(account_a="a", account_b="b", assigned_at="not-a-date")]

    assert _time_to_reshuffle(pairs, now) is True


def test_build_pairs_honors_partner_bounds() -> None:
    pool = [f"acc-{i:02d}" for i in range(10)]

    pairs = _build_pairs(pool)

    degree: Counter[str] = Counter()
    for account_a, account_b in pairs:
        assert account_a < account_b  # canonical ordering
        assert account_a != account_b  # no self-pairs
        assert {account_a, account_b} <= set(pool)
        degree[account_a] += 1
        degree[account_b] += 1
    # Every account initiates at least ``dialogue_partners_min`` pairs, so its
    # degree meets the floor. (No upper assert: an account may also be picked as
    # a partner by others, pushing its total degree above ``_partners_max``.)
    for account in pool:
        assert degree[account] >= settings.warming.dialogue_partners_min


@pytest.mark.asyncio
async def test_assign_pairs_drops_newly_ineligible_account() -> None:
    for i in (1, 2, 3):
        await _seed_warming(f"acc-{i}")
    await assign_pairs()

    # acc-3 becomes fail-health -> ineligible.
    await update_account_from_session_check(
        TelegramSessionCheckResult(
            account_id="acc-3",
            session_path="acc-3",
            status="account_error",
            is_temporary=False,
        ),
    )
    pairs = (await assign_pairs()).pairs

    covered = {pair.account_a for pair in pairs} | {pair.account_b for pair in pairs}
    assert "acc-3" not in covered
    assert (await get_partners("acc-3")).partners == []


@pytest.mark.asyncio
async def test_concurrent_assign_pairs_leaves_consistent_state() -> None:
    for i in (1, 2, 3):
        await _seed_warming(f"acc-{i}")

    await asyncio.gather(assign_pairs(), assign_pairs())

    pairs = await list_dialogue_pairs()
    # Serialized by _assign_lock: one writer at a time, so a single assigned_at.
    assert len({pair.assigned_at for pair in pairs}) == 1
    covered = {pair.account_a for pair in pairs} | {pair.account_b for pair in pairs}
    assert covered == {"acc-1", "acc-2", "acc-3"}


@pytest.mark.asyncio
async def test_try_claim_message_reply_atomic() -> None:
    """First claim wins; the second one (race) returns False without sending."""
    await create_account(AccountCreate(account_id="a"))
    await create_account(AccountCreate(account_id="b"))
    await record_dialogue_message("a", "b", "hi")
    incoming = await latest_unreplied_for("b")
    assert incoming is not None

    assert await try_claim_message_reply(incoming.id) is True
    assert await try_claim_message_reply(incoming.id) is False
    assert await latest_unreplied_for("b") is None
