"""The account pool a run reads with, and the per-pick eligibility check behind it."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from core.config import settings
from core.db import _get_engine, create_account, upsert_warming_state
from core.repositories.neurocomment import set_listener_running
from schemas.accounts import AccountCreate
from schemas.neurocomment_discovery import DiscoverySearchOutcome
from schemas.warming import WarmingStateWrite
from services.neurocomment import _discovery_state, _state
from services.neurocomment._discovery_pool import (
    AccountPool,
    SearchAccount,
    check_search_accounts,
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


async def _park(account_id: str) -> None:
    await _state.set_cooldown(account_id, datetime.now(UTC) + timedelta(hours=1))


def test_accounts_lists_every_account_still_in_the_pool_in_starting_order() -> None:
    pool = _pool(("a", None), ("b", True), ("c", False))

    assert [account.account_id for account in pool.accounts()] == ["a", "b", "c"]
    assert pool.size == 3
    assert pool.has("a") is True
    assert pool.has("ghost") is False


def test_premium_left_is_true_only_while_a_premium_account_still_has_reads(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A capped Premium account no longer counts, even though it stays ``has()``.

    Read live off the read count, not off pool membership: a stream waiting on
    ``prefer_premium`` must fall back to a plain account the moment the last Premium
    one caps out, not only once it floods or dies.
    """
    monkeypatch.setattr(settings.neurocomment, "discovery_max_reads_per_run", 1)
    pool = _pool(("plain", None), ("paid", True))

    assert pool.premium_left() is True
    assert pool.check("paid", charge=True) == "ok"
    assert pool.premium_left() is False
    assert pool.has("paid") is True
    # No Premium account at all: never "left" in the first place.
    assert _pool(("a", None), ("b", False)).premium_left() is False


@pytest.mark.asyncio
async def test_a_cooling_account_is_skipped_and_dropped() -> None:
    """Parked by somebody else mid-run: it leaves for good, the rest keep reading."""
    pool = _pool(("a", None), ("b", None))
    await _park("a")

    assert pool.check("a", charge=True) == "cooling"
    assert pool.has("a") is False
    assert pool.has("b") is True
    assert pool.empty is False


@pytest.mark.asyncio
async def test_a_flooded_account_leaves_and_the_pool_carries_on() -> None:
    pool = _pool(("a", None), ("b", None))

    assert await pool.report("a", flood_seconds=60) is None
    # The cooldown is recorded, exactly as the single-account run did.
    assert _state.in_cooldown("a", datetime.now(UTC)) is True
    assert pool.has("a") is False
    assert [account.account_id for account in pool.accounts()] == ["b"]


@pytest.mark.asyncio
async def test_the_last_account_leaving_ends_the_pool_with_its_reason() -> None:
    pool = _pool(("only", None))

    assert await pool.report("only", flood_seconds=60) == "flooded"
    assert pool.empty is True
    assert pool.has("only") is False


@pytest.mark.asyncio
async def test_consecutive_failures_drop_an_account_and_an_answer_resets_them(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The dead-session rule, per account: one wedged proxy must not end the whole run."""
    monkeypatch.setattr(settings.neurocomment, "discovery_max_consecutive_errors", 2)
    pool = _pool(("a", None), ("b", None))

    assert await pool.report("a", failed=True) is None
    assert await pool.report("a", failed=False) is None
    assert await pool.report("a", failed=True) is None
    assert pool.has("a") is True
    # The second failure in a row drops it; the pool is not empty, so no stop reason.
    assert await pool.report("a", failed=True) is None
    assert pool.has("a") is False
    assert pool.has("b") is True
    # No flood was involved, so nothing was written against the account.
    assert _state.in_cooldown("a", datetime.now(UTC)) is False
    # The pool's last account failing the same way IS the stop, named.
    assert await pool.report("b", failed=True) is None
    assert await pool.report("b", failed=True) == "aborted"


def test_each_account_has_its_own_wave_read_ceiling(monkeypatch: pytest.MonkeyPatch) -> None:
    """Charged reads cap at the ceiling per account; the pool itself stays.

    The shared budget is ``ceiling x pool size``, so after two drops one survivor could
    absorb all three shares — three times the traffic the ceiling exists to bound.
    """
    monkeypatch.setattr(settings.neurocomment, "discovery_max_reads_per_run", 2)
    pool = _pool(("a", None), ("b", None))

    assert pool.check("a", charge=True) == "ok"
    assert pool.check("a", charge=True) == "ok"
    assert pool.check("a", charge=True) == "capped"
    # Truncation, not a stop: the account is still here for the qualification probes,
    # which are bounded by the candidate limit rather than by this ceiling.
    assert pool.has("a") is True
    assert pool.empty is False
    # Probes (``charge=False``) are bounded elsewhere and never cap.
    assert pool.check("a", charge=False) == "ok"
    assert pool.check("a", charge=False) == "ok"


@pytest.mark.asyncio
async def test_check_refuses_an_unknown_or_never_signed_in_account() -> None:
    await create_account(AccountCreate(account_id="acc-fresh"))

    assert await check_search_accounts("mine", ["ghost"]) == DiscoverySearchOutcome(
        status="no_account", refused_account_id="ghost"
    )
    assert await check_search_accounts("mine", ["acc-fresh"]) == DiscoverySearchOutcome(
        status="no_account", refused_account_id="acc-fresh"
    )


@pytest.mark.asyncio
async def test_check_reports_busy_for_the_running_listener_and_for_warming() -> None:
    await seed_listener()
    await seed_account("acc-warm")
    await set_listener_running(running=True)
    await upsert_warming_state(WarmingStateWrite(account_id="acc-warm", state="active"))

    assert await check_search_accounts("mine", [LISTENER_ID]) == DiscoverySearchOutcome(
        status="account_busy", refused_account_id=LISTENER_ID
    )
    assert await check_search_accounts("mine", ["acc-warm"]) == DiscoverySearchOutcome(
        status="account_busy", refused_account_id="acc-warm"
    )


@pytest.mark.asyncio
async def test_check_reports_cooling_ahead_of_busy() -> None:
    """Both refuse; the Telegram-limit reason is the one the operator can act on."""
    await seed_account("acc-both")
    await upsert_warming_state(WarmingStateWrite(account_id="acc-both", state="active"))
    await _park("acc-both")

    assert await check_search_accounts("mine", ["acc-both"]) == DiscoverySearchOutcome(
        status="account_cooling", refused_account_id="acc-both"
    )


@pytest.mark.asyncio
async def test_check_names_an_account_another_campaigns_run_is_reading_with() -> None:
    """``already_running`` said the wrong thing: THIS campaign has no run.

    Another one holds the account — so the picker row, not the whole start, is what the
    operator must change.
    """
    await seed_account("acc-held")
    await seed_account("acc-free")
    assert _discovery_state.try_reserve("other", frozenset({"acc-held"})) is None

    assert await check_search_accounts("mine", ["acc-free", "acc-held"]) == DiscoverySearchOutcome(
        status="account_busy", refused_account_id="acc-held"
    )
    # The holder's own re-start is not an account collision: the claim reports it.
    assert await check_search_accounts("other", ["acc-held"]) == [
        SearchAccount("acc-held", name="acc-held")
    ]


@pytest.mark.asyncio
async def test_check_returns_every_pick_with_its_premium_flag_and_name() -> None:
    await seed_account("acc-paid", premium=True)
    await seed_account("acc-plain")

    assert await check_search_accounts("mine", ["acc-paid", "acc-plain"]) == [
        SearchAccount("acc-paid", premium=True, name="acc-paid"),
        SearchAccount("acc-plain", premium=None, name="acc-plain"),
    ]


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
    # The import label ("Fresh") is not the name: it is a file name, not a person.
    assert listed.items[2].name == "fresh"


@pytest.mark.asyncio
async def test_list_names_accounts_like_the_accounts_table() -> None:
    """Telegram first + last name, else phone, else id; the username rides separately."""
    await create_account(AccountCreate(account_id="acc-1", session_name="s", label="1_telethon"))
    await create_account(AccountCreate(account_id="acc-2", session_name="s2", phone="+7900"))
    await create_account(AccountCreate(account_id="acc-3", session_name="s3"))
    with _get_engine().begin() as connection:
        connection.exec_driver_sql(
            "UPDATE accounts SET first_name = 'Leon', last_name = 'Morales', username = 'leo'"
            " WHERE account_id = 'acc-1'"
        )

    listed = await list_search_accounts()

    assert [(item.name, item.username) for item in listed.items] == [
        ("+7900", None),
        ("acc-3", None),
        ("Leon Morales", "leo"),
    ]
