"""The account pool a run reads with, and the per-pick eligibility check behind it."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from core.config import settings
from core.db import create_account, upsert_warming_state
from core.repositories.neurocomment import set_listener_running
from schemas.accounts import AccountCreate
from schemas.warming import WarmingStateWrite
from services.neurocomment import _discovery_state, _state
from services.neurocomment._discovery_pool import (
    AccountPool,
    SearchAccount,
    check_search_account,
    list_search_accounts,
)
from tests.services.neurocomment.discovery_support import (
    LISTENER_ID,
    seed_account,
    seed_listener,
)

pytestmark = pytest.mark.usefixtures("isolate_discovery")


def _pool(*accounts: tuple[str, bool | None]) -> AccountPool:
    return AccountPool(SearchAccount(account_id, premium) for account_id, premium in accounts)


def _next(pool: AccountPool, count: int) -> list[str | None]:
    return [pool.acquire() for _ in range(count)]


async def _park(account_id: str) -> None:
    await _state.set_cooldown(account_id, datetime.now(UTC) + timedelta(hours=1))


def test_premium_accounts_lead_the_rotation_and_it_is_round_robin() -> None:
    """Telegram's limits are looser on Premium, so those absorb the opening reads."""
    pool = _pool(("plain", None), ("paid", True), ("free", False))

    assert pool.size == 3
    assert _next(pool, 4) == ["paid", "plain", "free", "paid"]


@pytest.mark.asyncio
async def test_a_cooling_account_is_skipped_and_dropped() -> None:
    """Parked by somebody else mid-run: it leaves for good, the rest keep reading."""
    pool = _pool(("a", None), ("b", None))
    await _park("a")

    assert _next(pool, 2) == ["b", "b"]
    assert pool.dropped_reason == "cooling"
    assert pool.empty is False


@pytest.mark.asyncio
async def test_a_flooded_account_leaves_and_the_pool_carries_on() -> None:
    pool = _pool(("a", None), ("b", None))

    ended = await pool.report("a", flood_seconds=60)

    assert ended is False
    assert pool.dropped_reason == "flooded"
    # The cooldown is recorded, exactly as the single-account run did.
    assert _state.in_cooldown("a", datetime.now(UTC)) is True
    assert _next(pool, 2) == ["b", "b"]


@pytest.mark.asyncio
async def test_the_last_account_leaving_ends_the_pool_with_its_reason() -> None:
    pool = _pool(("only", None))

    assert await pool.report("only", flood_seconds=60) is True
    assert pool.empty is True
    assert pool.dropped_reason == "flooded"
    assert pool.acquire() is None


@pytest.mark.asyncio
async def test_consecutive_failures_drop_an_account_and_an_answer_resets_them(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The dead-session rule, per account: one wedged proxy must not end the whole run."""
    monkeypatch.setattr(settings.neurocomment, "discovery_max_consecutive_errors", 2)
    pool = _pool(("a", None), ("b", None))

    assert await pool.report("a", failed=True) is False
    assert await pool.report("a", failed=False) is False
    assert await pool.report("a", failed=True) is False
    assert pool.dropped_reason is None
    assert await pool.report("a", failed=True) is False

    assert pool.dropped_reason == "aborted"
    assert _next(pool, 2) == ["b", "b"]
    # No flood was involved, so nothing was written against the account.
    assert _state.in_cooldown("a", datetime.now(UTC)) is False


@pytest.mark.asyncio
async def test_check_refuses_an_unknown_or_never_signed_in_account() -> None:
    await create_account(AccountCreate(account_id="acc-fresh"))

    assert await check_search_account("ghost") == "no_account"
    assert await check_search_account("acc-fresh") == "no_account"


@pytest.mark.asyncio
async def test_check_reports_busy_for_the_running_listener_and_for_warming() -> None:
    await seed_listener()
    await seed_account("acc-warm")
    await set_listener_running(running=True)
    await upsert_warming_state(WarmingStateWrite(account_id="acc-warm", state="active"))

    assert await check_search_account(LISTENER_ID) == "account_busy"
    assert await check_search_account("acc-warm") == "account_busy"


@pytest.mark.asyncio
async def test_check_reports_cooling_ahead_of_busy() -> None:
    """Both refuse; the Telegram-limit reason is the one the operator can act on."""
    await seed_account("acc-both")
    await upsert_warming_state(WarmingStateWrite(account_id="acc-both", state="active"))
    await _park("acc-both")

    assert await check_search_account("acc-both") == "account_cooling"


@pytest.mark.asyncio
async def test_check_returns_the_account_with_its_premium_flag() -> None:
    await seed_account("acc-paid", premium=True)

    assert await check_search_account("acc-paid") == SearchAccount("acc-paid", premium=True)


@pytest.mark.asyncio
async def test_list_orders_premium_first_and_names_every_busy_reason() -> None:
    await seed_account("zed", premium=True)
    await seed_account("alpha")
    await seed_account("held")
    await create_account(AccountCreate(account_id="fresh", label="Fresh"))
    await upsert_warming_state(WarmingStateWrite(account_id="zed", state="active"))
    await _park("alpha")
    # Another campaign's run holds ``held`` — the claim alone counts as running.
    assert _discovery_state.try_reserve("other", frozenset({"held"})) is None

    listed = await list_search_accounts()

    assert [(item.account_id, item.busy_reason) for item in listed.items] == [
        ("zed", "account_busy"),
        ("alpha", "account_cooling"),
        ("fresh", "no_session"),
        ("held", "account_busy"),
    ]
    assert listed.items[0].premium is True
    assert listed.items[2].name == "Fresh"
