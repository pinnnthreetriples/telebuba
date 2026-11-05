"""Own-authorization persistence: recorded once per hash, gone with the account."""

from __future__ import annotations

import asyncio
from typing import TYPE_CHECKING

import pytest

from core.db import configure_database, create_account
from core.repositories.account_own_authorizations import (
    fetch_own_authorizations,
    save_own_authorizations,
)
from core.repositories.accounts import _delete_account
from schemas.accounts import AccountCreate

if TYPE_CHECKING:
    from pathlib import Path

_AT = "2026-11-01T00:00:00+00:00"


@pytest.fixture(autouse=True)
def _isolate(tmp_path: Path) -> None:
    configure_database(tmp_path / "telebuba.db")


@pytest.mark.asyncio
async def test_hashes_are_recorded_per_account_and_a_repeat_is_ignored() -> None:
    await create_account(AccountCreate(account_id="acc-a"))
    await create_account(AccountCreate(account_id="acc-b"))

    await save_own_authorizations("acc-a", ["5", "-6", "5"], kind="web", recorded_at=_AT)
    await save_own_authorizations("acc-a", ["5"], kind="web", recorded_at="later")
    await save_own_authorizations("acc-a", [], kind="web", recorded_at=_AT)

    assert await fetch_own_authorizations("acc-a") == {"5": "web", "-6": "web"}
    assert await fetch_own_authorizations("acc-b") == {}


@pytest.mark.asyncio
async def test_deleting_the_account_removes_its_rows() -> None:
    await create_account(AccountCreate(account_id="acc-a"))
    await create_account(AccountCreate(account_id="acc-b"))
    await save_own_authorizations("acc-a", ["5"], kind="web", recorded_at=_AT)
    await save_own_authorizations("acc-b", ["7"], kind="web", recorded_at=_AT)

    await asyncio.to_thread(_delete_account, "acc-a")

    assert await fetch_own_authorizations("acc-a") == {}
    assert await fetch_own_authorizations("acc-b") == {"7": "web"}
