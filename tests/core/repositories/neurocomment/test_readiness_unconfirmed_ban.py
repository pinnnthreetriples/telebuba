"""Per-pair unconfirmed-ban counter (#47) — the rolling window, in SQL.

``stamp_unconfirmed_ban`` takes its window start from the caller: the 48h rule lives in
``services.neurocomment.bans``, this only applies whatever instant it is handed. These
pin the arithmetic that rule leans on — accumulate inside the window, restart outside it,
and never refill on a write the pair did not earn.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from core.db import (
    clear_unconfirmed_bans,
    create_account,
    stamp_unconfirmed_ban,
    upsert_readiness,
)
from schemas.accounts import AccountCreate

_CHANNEL = "@chan"


def _window_start(hours: float = 48.0) -> str:
    """The window the production rule would compute: ``channel_pause_hours * max_rounds``."""
    return (datetime.now(UTC) - timedelta(hours=hours)).isoformat()


async def _seed_pair(account_id: str = "acc-1") -> None:
    await create_account(
        AccountCreate(account_id=account_id, label=account_id, session_name=account_id),
    )
    await upsert_readiness(account_id, _CHANNEL, joined=True, captcha_passed=True, ready=True)


@pytest.mark.asyncio
async def test_refusals_inside_the_window_accumulate() -> None:
    await _seed_pair()

    assert await stamp_unconfirmed_ban("acc-1", _CHANNEL, _window_start()) == 1
    assert await stamp_unconfirmed_ban("acc-1", _CHANNEL, _window_start()) == 2


@pytest.mark.asyncio
async def test_a_refusal_older_than_the_window_starts_the_count_over() -> None:
    """48h clean and the pair is not stuck here — whatever it collected has lapsed."""
    await _seed_pair()
    assert await stamp_unconfirmed_ban("acc-1", _CHANNEL, _window_start()) == 1

    # A window that begins AFTER the stamp above: everything on record is now outside it,
    # which is exactly what the caller passes once 48h have gone by.
    moved_on = (datetime.now(UTC) + timedelta(hours=1)).isoformat()

    assert await stamp_unconfirmed_ban("acc-1", _CHANNEL, moved_on) == 1


@pytest.mark.asyncio
async def test_clearing_puts_the_whole_budget_back() -> None:
    """What a delivered comment does: proof the pair can write here after all."""
    await _seed_pair()
    await stamp_unconfirmed_ban("acc-1", _CHANNEL, _window_start())
    await stamp_unconfirmed_ban("acc-1", _CHANNEL, _window_start())

    await clear_unconfirmed_bans("acc-1", _CHANNEL)

    assert await stamp_unconfirmed_ban("acc-1", _CHANNEL, _window_start()) == 1


@pytest.mark.asyncio
async def test_the_count_belongs_to_the_pair_not_the_account() -> None:
    """One account refused in two chats is two independent budgets."""
    await _seed_pair()
    await upsert_readiness("acc-1", "@other", joined=True, captcha_passed=True, ready=True)

    await stamp_unconfirmed_ban("acc-1", _CHANNEL, _window_start())

    assert await stamp_unconfirmed_ban("acc-1", "@other", _window_start()) == 1


@pytest.mark.asyncio
async def test_a_re_onboard_does_not_refill_the_budget() -> None:
    """``upsert_readiness`` must not touch the counter — the pair is still a member here.

    The same trap ``stamp_join_request`` and ``stamp_rejoin_attempt`` are kept out of that
    write for: onboarding re-writes the row on every pass, so a reset riding along would
    hand the budget back before it could ever run out.
    """
    await _seed_pair()
    await stamp_unconfirmed_ban("acc-1", _CHANNEL, _window_start())

    await upsert_readiness("acc-1", _CHANNEL, joined=True, captcha_passed=True, ready=True)

    assert await stamp_unconfirmed_ban("acc-1", _CHANNEL, _window_start()) == 2


@pytest.mark.asyncio
async def test_a_pair_with_no_readiness_row_counts_nothing() -> None:
    """Nothing was recorded, so nothing can be spent — the caller reads 0, not 1."""
    await _seed_pair()

    assert await stamp_unconfirmed_ban("acc-1", "@never-tried", _window_start()) == 0
