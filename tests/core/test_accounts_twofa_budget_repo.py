"""The persisted budget of wrong cloud-password answers, on a real temp SQLite."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from core.db import (
    confirm_twofa_password_used,
    create_account,
    promote_account_twofa_pending,
    set_account_twofa_current_only,
    set_account_twofa_password,
    set_account_twofa_pending,
    twofa_password_candidates,
)
from core.repositories._accounts_twofa import (
    mark_account_twofa_pending_refused,
    record_twofa_rejection,
    remaining_twofa_attempts,
    reset_twofa_rejections,
)
from schemas.accounts import AccountCreate

_T0 = datetime(2026, 11, 25, 12, 0, tzinfo=UTC)
_WINDOW = 24.0


async def _reject(now: datetime, account_id: str = "a") -> int | None:
    return await record_twofa_rejection(account_id, window_hours=_WINDOW, now=now)


async def _left(now: datetime, account_id: str = "a") -> int:
    return await remaining_twofa_attempts(
        account_id, max_rejections=5, window_hours=_WINDOW, now=now
    )


async def _open() -> None:
    await create_account(AccountCreate(account_id="a"))


@pytest.mark.asyncio
async def test_a_fresh_account_has_the_whole_budget() -> None:
    await _open()
    assert await _left(_T0) == 5
    assert await _left(_T0, "missing") == 5


@pytest.mark.asyncio
async def test_each_rejection_spends_one_and_the_count_is_durable() -> None:
    await _open()
    assert [await _reject(_T0 + timedelta(minutes=i)) for i in range(3)] == [1, 2, 3]

    # Read back through fresh calls: nothing lives in the process.
    assert await _left(_T0 + timedelta(minutes=5)) == 2
    assert [await _reject(_T0 + timedelta(minutes=9)) for _ in range(3)] == [4, 5, 6]
    assert await _left(_T0 + timedelta(minutes=10)) == 0


@pytest.mark.asyncio
async def test_a_window_older_than_the_limit_starts_over() -> None:
    await _open()
    await _reject(_T0)
    await _reject(_T0 + timedelta(hours=1))

    assert await _left(_T0 + timedelta(hours=23)) == 3
    assert await _left(_T0 + timedelta(hours=25)) == 5
    assert await _reject(_T0 + timedelta(hours=25)) == 1
    # The new window runs from ITS first rejection, not from the old one.
    assert await _left(_T0 + timedelta(hours=48)) == 4


@pytest.mark.asyncio
async def test_an_unknown_account_records_nothing() -> None:
    await _open()
    assert await _reject(_T0, "missing") is None


@pytest.mark.asyncio
async def test_a_reset_gives_the_budget_back() -> None:
    await _open()
    await _reject(_T0)

    assert await reset_twofa_rejections("a") is True

    assert await _left(_T0) == 5
    assert await reset_twofa_rejections("missing") is False


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "ending",
    ["confirm", "promote", "replace", "current_only"],
)
async def test_an_accepted_or_replaced_password_gives_the_budget_back(ending: str) -> None:
    await _open()
    await set_account_twofa_pending("a", "new")
    await _reject(datetime.now(UTC))

    if ending == "confirm":
        await confirm_twofa_password_used("a", "anything")
    elif ending == "promote":
        await promote_account_twofa_pending("a", "new")
    elif ending == "replace":
        await set_account_twofa_password("a", "operator-typed")
    else:
        await set_account_twofa_current_only("a", "accepted")

    assert await _left(datetime.now(UTC)) == 5


@pytest.mark.asyncio
async def test_a_refused_pending_candidate_is_not_a_candidate_any_more() -> None:
    await _open()
    await set_account_twofa_password("a", "old")
    await set_account_twofa_pending("a", "seller-pw")
    assert await twofa_password_candidates("a") == ["old", "seller-pw"]

    await mark_account_twofa_pending_refused("a", "seller-pw")

    assert await twofa_password_candidates("a") == ["old"]
