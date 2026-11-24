"""Security snapshots: stored per account, replaced on save, gone with the account."""

from __future__ import annotations

import asyncio
from typing import TYPE_CHECKING

import pytest

from core.db import configure_database, create_account
from core.repositories.account_security_snapshots import (
    fetch_security_snapshot,
    forget_security_snapshot,
    save_security_snapshot,
)
from core.repositories.accounts import _delete_account
from schemas.accounts import AccountCreate
from schemas.security_snapshot import SecuritySnapshot

if TYPE_CHECKING:
    from pathlib import Path


@pytest.fixture(autouse=True)
def _isolate(tmp_path: Path) -> None:
    configure_database(tmp_path / "telebuba.db")


@pytest.mark.asyncio
async def test_a_snapshot_is_replaced_by_the_next_save_and_can_be_forgotten() -> None:
    first = SecuritySnapshot(has_password=False, has_recovery=False, email_unconfirmed=False)
    second = SecuritySnapshot(
        has_password=True, has_recovery=True, pending_reset_date="d", email_unconfirmed=True
    )

    await save_security_snapshot("acc", first)
    await save_security_snapshot("acc", second)
    stored = await fetch_security_snapshot("acc")

    assert stored is not None
    assert stored.model_dump(exclude={"checked_at"}) == second.model_dump(exclude={"checked_at"})
    assert stored.checked_at is not None
    await forget_security_snapshot("acc")
    assert await fetch_security_snapshot("acc") is None


@pytest.mark.asyncio
async def test_the_snapshot_goes_with_the_account() -> None:
    await create_account(AccountCreate(account_id="acc"))
    await save_security_snapshot(
        "acc", SecuritySnapshot(has_password=True, has_recovery=False, email_unconfirmed=False)
    )

    await asyncio.to_thread(_delete_account, "acc")

    assert await fetch_security_snapshot("acc") is None
