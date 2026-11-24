"""The durable "Telegram refused the pending candidate" mark on ``accounts``."""

from __future__ import annotations

import pytest

from core.db import (
    confirm_twofa_password_used,
    create_account,
    promote_account_twofa_pending,
    set_account_twofa_current_only,
    set_account_twofa_password,
    set_account_twofa_pending,
)
from core.repositories._accounts_twofa import (
    fetch_account_twofa_state,
    mark_account_twofa_pending_refused,
)
from schemas.accounts import AccountCreate


async def _refused_pending(account_id: str, *, current: str | None = None) -> None:
    await create_account(AccountCreate(account_id=account_id))
    if current is not None:
        await set_account_twofa_password(account_id, current)
    await set_account_twofa_pending(account_id, "seller-pw")
    assert await mark_account_twofa_pending_refused(account_id, "seller-pw") is True


@pytest.mark.asyncio
async def test_a_fresh_account_has_nothing_pending_and_nothing_refused() -> None:
    await create_account(AccountCreate(account_id="a"))

    state = await fetch_account_twofa_state("a")

    assert (state.current, state.pending, state.pending_refused) == (None, None, False)
    assert await fetch_account_twofa_state("missing") == (None, None, False)


@pytest.mark.asyncio
async def test_a_refusal_is_remembered_for_the_pending_value() -> None:
    await _refused_pending("a", current="old")

    state = await fetch_account_twofa_state("a")

    assert (state.current, state.pending, state.pending_refused) == ("old", "seller-pw", True)


@pytest.mark.asyncio
async def test_a_refusal_of_a_value_that_is_not_pending_is_not_recorded() -> None:
    """Compare-and-swap: a verdict about another value says nothing about this candidate."""
    await create_account(AccountCreate(account_id="a"))
    await set_account_twofa_pending("a", "seller-pw")

    assert await mark_account_twofa_pending_refused("a", "other") is False
    assert await mark_account_twofa_pending_refused("missing", "seller-pw") is False

    assert (await fetch_account_twofa_state("a")).pending_refused is False


@pytest.mark.asyncio
async def test_staging_a_value_clears_the_mark() -> None:
    await _refused_pending("a")

    await set_account_twofa_pending("a", "new-pw")

    state = await fetch_account_twofa_state("a")
    assert (state.pending, state.pending_refused) == ("new-pw", False)


@pytest.mark.asyncio
async def test_clearing_the_pending_value_clears_the_mark() -> None:
    await _refused_pending("a")

    await set_account_twofa_pending("a", None)
    await set_account_twofa_pending("a", "seller-pw")

    assert (await fetch_account_twofa_state("a")).pending_refused is False


@pytest.mark.asyncio
async def test_promoting_the_candidate_clears_the_mark() -> None:
    await _refused_pending("a")

    assert await promote_account_twofa_pending("a", "seller-pw") is True

    assert await fetch_account_twofa_state("a") == ("seller-pw", None, False)


@pytest.mark.asyncio
async def test_a_confirmed_use_of_the_candidate_clears_the_mark() -> None:
    await _refused_pending("a")

    assert await confirm_twofa_password_used("a", "seller-pw") is True

    assert (await fetch_account_twofa_state("a")).pending_refused is False


@pytest.mark.asyncio
async def test_setting_the_password_drops_the_candidate_and_its_mark() -> None:
    await _refused_pending("a")

    await set_account_twofa_password("a", "typed-pw")
    await set_account_twofa_pending("a", "later-pw")

    assert await fetch_account_twofa_state("a") == ("typed-pw", "later-pw", False)


@pytest.mark.asyncio
async def test_forgetting_the_password_drops_the_mark() -> None:
    await _refused_pending("a")

    await set_account_twofa_password("a", None)
    await set_account_twofa_pending("a", "again")

    assert await fetch_account_twofa_state("a") == (None, "again", False)


@pytest.mark.asyncio
async def test_a_current_only_write_leaves_the_candidate_and_its_mark_alone() -> None:
    """A password Telegram accepted at sign-in says nothing about the refused candidate."""
    await _refused_pending("a")

    await set_account_twofa_current_only("a", "accepted-pw")

    assert await fetch_account_twofa_state("a") == ("accepted-pw", "seller-pw", True)
