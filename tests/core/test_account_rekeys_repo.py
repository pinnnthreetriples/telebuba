"""Re-key persistence: one row per account, the due ones oldest first, gone with the account."""

from __future__ import annotations

import asyncio
from typing import TYPE_CHECKING

import pytest

from core.db import configure_database, create_account
from core.repositories.account_rekeys import fetch_rekey, list_due_rekeys, save_rekey
from core.repositories.accounts import _delete_account
from schemas.accounts import AccountCreate
from schemas.session_rekey import StoredRekey

if TYPE_CHECKING:
    from pathlib import Path


def _rekey(
    account_id: str, *, close_after: str | None, closed_at: str | None = None
) -> StoredRekey:
    return StoredRekey(
        account_id=account_id,
        old_hash="-123",
        rekeyed_at="2026-11-01T00:00:00+00:00",
        close_after=close_after,
        closed_at=closed_at,
    )


@pytest.fixture(autouse=True)
def _isolate(tmp_path: Path) -> None:
    configure_database(tmp_path / "telebuba.db")


@pytest.mark.asyncio
async def test_a_saved_rekey_reads_back_and_a_second_replaces_it() -> None:
    await create_account(AccountCreate(account_id="acc-a"))
    assert await fetch_rekey("acc-a") is None

    await save_rekey(_rekey("acc-a", close_after="2026-11-02T00:00:00+00:00"))
    assert await fetch_rekey("acc-a") == _rekey("acc-a", close_after="2026-11-02T00:00:00+00:00")

    ended = _rekey("acc-a", close_after=None, closed_at="2026-11-02T00:00:05+00:00")
    await save_rekey(ended)
    assert await fetch_rekey("acc-a") == ended


@pytest.mark.asyncio
async def test_a_problem_is_stored_and_a_problem_row_is_never_due() -> None:
    await create_account(AccountCreate(account_id="acc-a"))
    stuck = _rekey("acc-a", close_after=None).model_copy(update={"problem": "old_key_unknown"})

    await save_rekey(stuck)

    assert await fetch_rekey("acc-a") == stuck
    assert await list_due_rekeys("2030-01-01T00:00:00+00:00") == []


@pytest.mark.asyncio
async def test_only_unended_rekeys_whose_time_has_come_are_due_oldest_first() -> None:
    for account_id in ("acc-a", "acc-b", "acc-c", "acc-d"):
        await create_account(AccountCreate(account_id=account_id))
    await save_rekey(_rekey("acc-a", close_after="2026-11-02T10:00:00+00:00"))
    await save_rekey(_rekey("acc-b", close_after="2026-11-02T09:00:00+00:00"))
    await save_rekey(_rekey("acc-c", close_after="2026-11-03T09:00:00+00:00"))
    await save_rekey(
        _rekey("acc-d", close_after=None, closed_at="2026-11-01T01:00:00+00:00"),
    )

    due = await list_due_rekeys("2026-11-02T12:00:00+00:00")

    assert [row.account_id for row in due] == ["acc-b", "acc-a"]


@pytest.mark.asyncio
async def test_deleting_the_account_removes_its_rekey_only() -> None:
    await create_account(AccountCreate(account_id="acc-a"))
    await create_account(AccountCreate(account_id="acc-b"))
    await save_rekey(_rekey("acc-a", close_after="2026-11-02T00:00:00+00:00"))
    await save_rekey(_rekey("acc-b", close_after="2026-11-02T00:00:00+00:00"))

    await asyncio.to_thread(_delete_account, "acc-a")

    assert await fetch_rekey("acc-a") is None
    assert await fetch_rekey("acc-b") is not None
