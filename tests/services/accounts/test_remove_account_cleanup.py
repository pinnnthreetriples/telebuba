"""Per-account state a delete must take with it, beyond the rows core already purges.

``_delete_account`` clears ``neurocomment_cooldowns`` precisely so a re-imported account
reusing the id is not born parked. The live map those rows only back up is a service
global that ``core`` may not reach, so the delete is the one place both halves can be
dropped together.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING

import pytest

from schemas.accounts import AccountCreate
from services.accounts import add_account, remove_account
from services.neurocomment._state import in_cooldown, reset_for_tests, set_cooldown

if TYPE_CHECKING:
    from collections.abc import Iterator

_ACCOUNT = "acc-1"


@pytest.fixture(autouse=True)
def _isolate_cooldowns() -> Iterator[None]:
    reset_for_tests()
    yield
    reset_for_tests()


async def _flooded_account(account_id: str) -> None:
    await add_account(AccountCreate(account_id=account_id, label="A", session_name=account_id))
    await set_cooldown(account_id, datetime.now(UTC) + timedelta(hours=2))


@pytest.mark.asyncio
async def test_removing_an_account_drops_its_live_cooldown() -> None:
    """Otherwise the durable half ran backwards, and warming's Start inherited it.

    Operator sequence: the account floods, they delete and re-import it for a clean
    slate, and Start answers 409 ``account_cooling`` from a deadline belonging to an
    account that no longer exists — while a backend RESTART fixes it, because the row
    the map is rehydrated from was purged with the account.
    """
    await _flooded_account(_ACCOUNT)
    assert in_cooldown(_ACCOUNT, datetime.now(UTC)) is True

    await remove_account(_ACCOUNT)

    assert in_cooldown(_ACCOUNT, datetime.now(UTC)) is False


@pytest.mark.asyncio
async def test_removing_an_account_leaves_every_other_account_parked() -> None:
    """The purge is keyed by account, not a convenient way to clear the whole map."""
    await _flooded_account(_ACCOUNT)
    await _flooded_account("acc-keep")
    await set_cooldown("acc-keep", datetime.now(UTC) + timedelta(hours=2), channel="@chat")

    await remove_account(_ACCOUNT)

    assert in_cooldown("acc-keep", datetime.now(UTC)) is True
    assert in_cooldown("acc-keep", datetime.now(UTC), channel="@chat") is True
