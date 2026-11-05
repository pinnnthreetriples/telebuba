"""The pending cloud-password candidate: staging, promotion, candidates, confirm-on-use."""

from __future__ import annotations

import pytest

from core.db import (
    confirm_twofa_password_used,
    create_account,
    fetch_account_twofa_pair,
    fetch_account_twofa_password,
    promote_account_twofa_pending,
    set_account_twofa_current_only,
    set_account_twofa_password,
    set_account_twofa_pending,
    twofa_password_candidates,
)
from schemas.accounts import AccountCreate


async def _account(account_id: str) -> None:
    await create_account(AccountCreate(account_id=account_id))


@pytest.mark.asyncio
async def test_staging_is_independent_of_the_current_password() -> None:
    await _account("a")
    await set_account_twofa_password("a", "old")

    assert await set_account_twofa_pending("a", "new") is True

    assert await fetch_account_twofa_pair("a") == ("old", "new")
    # The legacy reader still sees only the current one.
    assert await fetch_account_twofa_password("a") == "old"


@pytest.mark.asyncio
async def test_candidates_list_the_current_password_first() -> None:
    await _account("a")
    assert await twofa_password_candidates("a") == []
    await set_account_twofa_pending("a", "new")
    assert await twofa_password_candidates("a") == ["new"]
    await set_account_twofa_password("a", "old")
    await set_account_twofa_pending("a", "new")
    assert await twofa_password_candidates("a") == ["old", "new"]
    assert await twofa_password_candidates("missing") == []


@pytest.mark.asyncio
async def test_staging_for_an_unknown_account_reports_failure() -> None:
    assert await set_account_twofa_pending("ghost", "x") is False


@pytest.mark.asyncio
async def test_promotion_moves_pending_to_current_atomically() -> None:
    await _account("a")
    await set_account_twofa_password("a", "old")
    await set_account_twofa_pending("a", "new")

    assert await promote_account_twofa_pending("a", "new") is True

    assert await fetch_account_twofa_pair("a") == ("new", None)
    assert await promote_account_twofa_pending("a", "new") is False


@pytest.mark.asyncio
async def test_promotion_is_a_compare_and_swap_on_the_pending_value() -> None:
    """A pending value that is not the one being promoted is never promoted in its place."""
    await _account("a")
    await set_account_twofa_password("a", "old")
    await set_account_twofa_pending("a", "newer")

    assert await promote_account_twofa_pending("a", "stale-read") is False
    assert await confirm_twofa_password_used("a", "stale-read") is False

    assert await fetch_account_twofa_pair("a") == ("old", "newer")


@pytest.mark.asyncio
async def test_setting_the_current_one_only_keeps_the_pending_candidate() -> None:
    await _account("a")
    await set_account_twofa_pending("a", "maybe")

    assert await set_account_twofa_current_only("a", "typed") is True

    assert await fetch_account_twofa_pair("a") == ("typed", "maybe")
    assert await set_account_twofa_current_only("ghost", "x") is False


@pytest.mark.asyncio
async def test_setting_or_clearing_the_current_password_drops_the_pending_one() -> None:
    await _account("a")
    await set_account_twofa_pending("a", "new")
    await set_account_twofa_password("a", "verified")
    assert await fetch_account_twofa_pair("a") == ("verified", None)

    await set_account_twofa_pending("a", "again")
    await set_account_twofa_password("a", None)
    assert await fetch_account_twofa_pair("a") == (None, None)


@pytest.mark.asyncio
async def test_a_login_that_succeeded_with_the_pending_one_promotes_it() -> None:
    await _account("a")
    await set_account_twofa_password("a", "old")
    await set_account_twofa_pending("a", "new")

    assert await confirm_twofa_password_used("a", "old") is False
    assert await fetch_account_twofa_pair("a") == ("old", "new")
    assert await confirm_twofa_password_used("a", "unrelated") is False

    assert await confirm_twofa_password_used("a", "new") is True
    assert await fetch_account_twofa_pair("a") == ("new", None)
