"""Per-pair unconfirmed-ban counter (#47) — the window AND the minimum interval, in SQL.

``stamp_unconfirmed_ban`` takes both instants from the caller: the 48h rule lives in
``services.neurocomment.bans``, this only applies whatever it is handed — but it applies
both in ONE statement, which is what makes two refusals racing on the same pair impossible
to count twice. These pin the arithmetic that rule leans on: accumulate inside the window,
restart outside it, refuse anything inside the interval without moving the stamp, and never
refill on a write the pair did not earn.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from core.db import (  # type: ignore[attr-defined]
    _get_engine,
    clear_unconfirmed_bans,
    create_account,
    create_campaign,
    deactivate_channel,
    fetch_readiness,
    link_channel_to_campaign,
    mark_pair_banned,
    stamp_unconfirmed_ban,
    unconfirmed_ban_is_countable,
    upsert_readiness,
)
from schemas.accounts import AccountCreate
from schemas.neurocomment import CampaignCreate

_CHANNEL = "@chan"


def _window_start(hours: float = 48.0) -> str:
    """The window the production rule would compute: ``channel_pause_hours * max_rounds``."""
    return (datetime.now(UTC) - timedelta(hours=hours)).isoformat()


def _interval_elapsed() -> str:
    """The minimum interval as a caller that has already waited it out passes it.

    An instant AFTER everything on record, which is what "a day later" looks like to the
    clause — without a test sleeping through one. ``_window_start(0.0)`` would not do: the
    stamp this has to be newer than was written microseconds ago.
    """
    return (datetime.now(UTC) + timedelta(hours=1)).isoformat()


async def _count_a_refusal(
    account_id: str = "acc-1",
    channel: str = _CHANNEL,
    *,
    window_start: str | None = None,
    interval_start: str | None = None,
) -> int:
    """One refusal, as ``bans.register_unconfirmed_ban`` spends it.

    Both instants default to "the interval has run out, and we are inside the window", so
    each test states only the clock it is actually about.
    """
    return await stamp_unconfirmed_ban(
        account_id,
        channel,
        window_start or _window_start(),
        interval_start or _interval_elapsed(),
    )


async def _seed_pair(account_id: str = "acc-1") -> None:
    await create_account(
        AccountCreate(account_id=account_id, label=account_id, session_name=account_id),
    )
    await upsert_readiness(account_id, _CHANNEL, joined=True, captcha_passed=True, ready=True)


def _stamped_at(account_id: str = "acc-1", channel: str = _CHANNEL) -> str | None:
    """The pair's raw ``unconfirmed_ban_at`` — the anchor both clocks are measured from.

    Read off the table directly: it is this counter's own bookkeeping and deliberately not
    part of the readiness model the board and the API are served.
    """
    with _get_engine().connect() as connection:
        return connection.exec_driver_sql(
            "SELECT unconfirmed_ban_at FROM neurocomment_readiness "
            "WHERE account_id = ? AND channel = ?",
            (account_id, channel),
        ).scalar()


def _counted(account_id: str = "acc-1", channel: str = _CHANNEL) -> int:
    """The pair's raw ``unconfirmed_bans`` — read off the table for ``_stamped_at``'s reason."""
    with _get_engine().connect() as connection:
        return connection.exec_driver_sql(
            "SELECT unconfirmed_bans FROM neurocomment_readiness "
            "WHERE account_id = ? AND channel = ?",
            (account_id, channel),
        ).scalar()


async def _a_linked_channel_with_a_spent_refusal() -> str:
    """A pair one refusal into its budget, on a channel linked to an active campaign."""
    campaign = await create_campaign(CampaignCreate(name="A", prompt="p"))
    await link_channel_to_campaign(campaign.campaign_id, _CHANNEL)
    await _seed_pair()
    await _count_a_refusal()
    return campaign.campaign_id


@pytest.mark.asyncio
async def test_refusals_inside_the_window_accumulate() -> None:
    await _seed_pair()

    assert await _count_a_refusal() == 1
    assert await _count_a_refusal() == 2


@pytest.mark.asyncio
async def test_a_refusal_older_than_the_window_starts_the_count_over() -> None:
    """48h clean and the pair is not stuck here — whatever it collected has lapsed."""
    await _seed_pair()
    assert await _count_a_refusal() == 1

    # A window that begins AFTER the stamp above: everything on record is now outside it,
    # which is exactly what the caller passes once 48h have gone by.
    moved_on = (datetime.now(UTC) + timedelta(hours=1)).isoformat()

    assert await _count_a_refusal(window_start=moved_on) == 1


@pytest.mark.asyncio
async def test_a_refusal_inside_the_minimum_interval_counts_nothing() -> None:
    """The guard the rule used to keep in Python, an ``await`` away from this write.

    Two refusals whose coroutines interleaved both passed it and both counted; here the
    same statement that increments is the one that decides, so the second is charged to
    nobody — 0, the same answer a pair with no row at all gets.
    """
    await _seed_pair()
    assert await _count_a_refusal() == 1

    assert await _count_a_refusal(interval_start=_window_start(24.0)) == 0


@pytest.mark.asyncio
async def test_a_refusal_inside_the_interval_does_not_move_the_stamp() -> None:
    """Nothing is written at all, so the clock keeps running from the COUNTED refusal.

    Were the stamp refreshed, a channel with a steady queue of posts would push the next
    countable refusal a full interval further out every time, and the budget could never
    run down.
    """
    await _seed_pair()
    await _count_a_refusal()
    counted_at = _stamped_at()

    assert await _count_a_refusal(interval_start=_window_start(24.0)) == 0

    assert _stamped_at() == counted_at


@pytest.mark.asyncio
async def test_the_countable_read_answers_the_same_clause_as_the_stamp() -> None:
    """The cheap pre-check that keeps @SpamBot off a refusal the interval already refuses.

    Not a guard — it cannot be, being a read — so what matters is only that it never says
    "countable" where the stamp would answer 0.
    """
    await _seed_pair()

    assert await unconfirmed_ban_is_countable("acc-1", "@never-tried", _window_start(24.0)) is False
    assert await unconfirmed_ban_is_countable("acc-1", _CHANNEL, _window_start(24.0)) is True

    await _count_a_refusal()

    assert await unconfirmed_ban_is_countable("acc-1", _CHANNEL, _window_start(24.0)) is False
    assert await unconfirmed_ban_is_countable("acc-1", _CHANNEL, _interval_elapsed()) is True


@pytest.mark.asyncio
async def test_clearing_puts_the_whole_budget_back() -> None:
    """What a delivered comment does: proof the pair can write here after all."""
    await _seed_pair()
    await _count_a_refusal()
    await _count_a_refusal()

    await clear_unconfirmed_bans("acc-1", _CHANNEL)

    assert await _count_a_refusal() == 1


@pytest.mark.asyncio
async def test_the_count_belongs_to_the_pair_not_the_account() -> None:
    """One account refused in two chats is two independent budgets."""
    await _seed_pair()
    await upsert_readiness("acc-1", "@other", joined=True, captcha_passed=True, ready=True)

    await _count_a_refusal()

    assert await _count_a_refusal(channel="@other") == 1


@pytest.mark.asyncio
async def test_a_re_onboard_does_not_refill_the_budget() -> None:
    """``upsert_readiness`` must not touch the counter — the pair is still a member here.

    The same trap ``stamp_join_request`` and ``stamp_rejoin_attempt`` are kept out of that
    write for: onboarding re-writes the row on every pass, so a reset riding along would
    hand the budget back before it could ever run out.
    """
    await _seed_pair()
    await _count_a_refusal()

    await upsert_readiness("acc-1", _CHANNEL, joined=True, captcha_passed=True, ready=True)

    assert await _count_a_refusal() == 2


@pytest.mark.asyncio
async def test_a_pair_with_no_readiness_row_counts_nothing() -> None:
    """Nothing was recorded, so nothing can be spent — the caller reads 0, not 1."""
    await _seed_pair()

    assert await _count_a_refusal(channel="@never-tried") == 0


@pytest.mark.asyncio
async def test_re_linking_the_channel_hands_the_whole_budget_back() -> None:
    """Linking a channel is a fresh start, and this counter is one of the per-pair ones.

    Every give-up log ends by telling the operator to link the channel again, which is why
    ``join_request_attempts`` and ``rejoin_attempts`` already reset there. A re-linked channel
    whose pairs kept half a budget would ban the first of them on its very first refusal — and
    ``count`` alone is not enough: with the stamp left behind, the next refusal would also be
    inside an interval nobody is serving.
    """
    campaign_id = await _a_linked_channel_with_a_spent_refusal()
    await deactivate_channel(campaign_id, _CHANNEL)

    await link_channel_to_campaign(campaign_id, _CHANNEL)

    assert (_counted(), _stamped_at()) == (0, None)
    # ...which is a whole budget, not a cleared column: the next refusal reads as the first.
    assert await _count_a_refusal() == 1


@pytest.mark.asyncio
async def test_a_banned_pair_stays_banned_across_the_re_link_that_clears_its_counter() -> None:
    """``banned`` is sticky by design (#30) and deliberately NOT part of the fresh start.

    The counters reset because a re-link is another attempt at the channel; the verdict does
    not, because the hint printed beside it tells the operator to add another ACCOUNT. Resetting
    it here would quietly offer the un-ban path the domain says does not exist.
    """
    campaign_id = await _a_linked_channel_with_a_spent_refusal()
    await mark_pair_banned("acc-1", _CHANNEL)
    await deactivate_channel(campaign_id, _CHANNEL)

    await link_channel_to_campaign(campaign_id, _CHANNEL)

    readiness = await fetch_readiness("acc-1", _CHANNEL)
    assert readiness is not None
    assert readiness.banned is True
    assert (_counted(), _stamped_at()) == (0, None)
