"""The survival pass re-asks @SpamBot about spam-blocked accounts no warming loop asks.

With no day-7/day-30 probe due, one pass asks about at most one account with a confirmed
spam block on record, once its last answer is ``block_recheck_hours`` old, under the
probes' retry and per-proxy spacing. Accounts a warming loop or another feature holds
are never asked. A stored clean answer newer than the block lifts it without asking.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING

import pytest

from core.config import settings
from core.db import (
    create_account,
    fetch_warming_state,
    upsert_spam_status,
    upsert_warming_state,
)
from core.repositories.lifecycle import append_lifecycle_event
from schemas.accounts import AccountCreate
from schemas.spam_status import SpamStatusVerdict
from schemas.warming import WarmingStateWrite
from services import _account_owner
from services.survival import _probe_loop, run_probe_pass
from services.survival._due import BlockRecheckCandidate, ProbePacing, pick_block_recheck

if TYPE_CHECKING:
    from collections.abc import Iterator

_BLOCKED_AT = "2026-10-01T00:00:00+00:00"
_HOUR = timedelta(hours=1)
_NOW = datetime(2026, 10, 20, tzinfo=UTC)


@pytest.fixture(autouse=True)
def _fresh_state() -> Iterator[None]:
    _probe_loop.reset_for_tests()
    _account_owner.reset_for_tests()
    yield
    _probe_loop.reset_for_tests()
    _account_owner.reset_for_tests()


@pytest.fixture
def asks(monkeypatch: pytest.MonkeyPatch) -> list[str]:
    asked: list[str] = []

    async def refresh(account_id: str, *, force: bool = False) -> SpamStatusVerdict:  # noqa: ARG001
        asked.append(account_id)
        return SpamStatusVerdict(
            account_id=account_id, status="limited", checked_at=_NOW.isoformat()
        )

    monkeypatch.setattr(_probe_loop, "refresh_spam_status", refresh)
    return asked


async def _blocked(account_id: str, state: str = "spam_blocked") -> None:
    await create_account(AccountCreate(account_id=account_id))
    await upsert_warming_state(
        WarmingStateWrite(
            account_id=account_id,
            state=state,  # ty: ignore[invalid-argument-type]
            spam_blocked_at=_BLOCKED_AT,
        )
    )


def _pacing() -> ProbePacing:
    return ProbePacing(now=_NOW, grace=timedelta(hours=36), spacing=_HOUR, retry=24 * _HOUR)


def _candidate(account_id: str, proxy: str, hours_ago: float | None) -> BlockRecheckCandidate:
    last = None if hours_ago is None else _NOW - timedelta(hours=hours_ago)
    return BlockRecheckCandidate(account_id, proxy, last)


def test_an_account_is_due_once_its_last_answer_is_old_enough() -> None:
    interval = 72 * _HOUR
    recent = _candidate("a", "p1", 10)
    old = _candidate("b", "p2", 80)

    assert pick_block_recheck([recent], {}, {}, _pacing(), interval) is None
    assert pick_block_recheck([recent, old], {}, {}, _pacing(), interval) == old


def test_never_asked_goes_first_then_the_longest_unasked() -> None:
    interval = 72 * _HOUR
    never = _candidate("a", "p1", None)
    oldest = _candidate("b", "p2", 500)
    old = _candidate("c", "p3", 100)

    assert pick_block_recheck([old, oldest, never], {}, {}, _pacing(), interval) == never
    assert pick_block_recheck([old, oldest], {}, {}, _pacing(), interval) == oldest


def test_the_probes_retry_and_proxy_spacing_apply() -> None:
    interval = 72 * _HOUR
    due = _candidate("a", "p1", None)

    tried = {"a": _NOW - _HOUR}
    assert pick_block_recheck([due], {}, tried, _pacing(), interval) is None
    spaced = {"p1": _NOW - timedelta(minutes=5)}
    assert pick_block_recheck([due], spaced, {}, _pacing(), interval) is None


@pytest.mark.asyncio
async def test_a_pass_with_no_probe_due_rechecks_a_parked_spam_block(asks: list[str]) -> None:
    await _blocked("a")

    assert await run_probe_pass(now=_NOW) == "a"
    assert asks == ["a"]
    # The retry holds the same account back on the next pass.
    assert await run_probe_pass(now=_NOW + _HOUR) is None
    assert asks == ["a"]


@pytest.mark.asyncio
async def test_a_recent_answer_waits_out_the_recheck_interval(asks: list[str]) -> None:
    await _blocked("a")
    await append_lifecycle_event("a", "spambot_verdict", {"status": "limited"})

    assert await run_probe_pass(now=datetime.now(UTC)) is None
    later = datetime.now(UTC) + timedelta(hours=settings.survival.block_recheck_hours + 1)
    assert await run_probe_pass(now=later) == "a"
    assert asks == ["a"]


@pytest.mark.asyncio
async def test_a_running_warming_loop_or_a_held_account_is_never_rechecked(
    asks: list[str],
) -> None:
    await _blocked("warming", state="sleeping")  # its own cycle asks @SpamBot
    await _blocked("held", state="idle")
    _account_owner.take_over("held", "chat_broadcast", "campaign-1")
    await _blocked("lent", state="idle")
    _account_owner.lend("lent", "search-1")

    assert await run_probe_pass(now=_NOW) is None
    assert asks == []


@pytest.mark.asyncio
async def test_a_stored_clean_answer_lifts_the_block_without_asking(asks: list[str]) -> None:
    await _blocked("a", state="idle")
    await upsert_spam_status(
        SpamStatusVerdict(account_id="a", status="clean", checked_at="2026-10-02T00:00:00+00:00")
    )

    assert await run_probe_pass(now=_NOW) is None

    assert asks == []
    record = await fetch_warming_state("a")
    assert record is not None
    assert record.spam_blocked_at is None


@pytest.mark.asyncio
async def test_an_account_without_a_block_is_not_rechecked(asks: list[str]) -> None:
    await create_account(AccountCreate(account_id="a"))
    await upsert_warming_state(WarmingStateWrite(account_id="a", state="idle"))

    assert await run_probe_pass(now=_NOW) is None
    assert asks == []


@pytest.mark.asyncio
async def test_a_stored_clean_answer_lifts_even_a_running_accounts_block(asks: list[str]) -> None:
    await _blocked("a", state="sleeping")
    await upsert_spam_status(
        SpamStatusVerdict(account_id="a", status="clean", checked_at="2026-10-02T00:00:00+00:00")
    )

    assert await run_probe_pass(now=_NOW) is None

    assert asks == [], "a running loop asks for itself; the stored answer only lifts"
    record = await fetch_warming_state("a")
    assert record is not None
    assert (record.state, record.spam_blocked_at) == ("sleeping", None)
