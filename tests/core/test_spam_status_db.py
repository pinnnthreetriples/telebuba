"""Tests for the spam-status cache repository reads in ``core.db``."""

from __future__ import annotations

from typing import TYPE_CHECKING

import pytest

from core.db import (
    configure_database,
    create_account,
    list_spam_statuses_by_ids,
    upsert_spam_status,
)
from schemas.accounts import AccountCreate
from schemas.spam_status import SpamStatusVerdict

if TYPE_CHECKING:
    from pathlib import Path


def _verdict(account_id: str) -> SpamStatusVerdict:
    return SpamStatusVerdict(
        account_id=account_id,
        status="clean",
        checked_at="2026-07-11T00:00:00Z",
    )


@pytest.mark.asyncio
async def test_list_spam_statuses_by_ids_scopes_and_guards_empty(tmp_path: Path) -> None:
    configure_database(tmp_path / "telebuba.db")
    for acc in ("acc-1", "acc-2"):
        await create_account(AccountCreate(account_id=acc))
        await upsert_spam_status(_verdict(acc))

    scoped = await list_spam_statuses_by_ids(["acc-1"])

    assert set(scoped) == {"acc-1"}
    assert scoped["acc-1"].status == "clean"
    assert await list_spam_statuses_by_ids([]) == {}
