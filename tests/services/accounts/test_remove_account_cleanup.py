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
from services.accounts.twofa import _TWOFA_LOCKS, twofa_lock
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


@pytest.mark.asyncio
async def test_removing_an_account_drops_its_twofa_lock() -> None:
    """The third per-account registry, and the only one the delete had not been told about.

    ``remove_account`` deliberately drops the post-listener generation and the
    neurocomment cooldown map for the same reason: both are keyed by account id, and
    nothing else ever drops those keys, so an app that outlives many deletes
    accumulates one dead entry per account. ``_TWOFA_LOCKS`` was added later and was
    never included, so it leaked an ``asyncio.Lock`` per account that ever set a
    cloud password — and a re-imported id reusing that key would take a lock bound to
    an event loop that may no longer be the running one.
    """
    await add_account(AccountCreate(account_id=_ACCOUNT, label="A", session_name=_ACCOUNT))
    twofa_lock(_ACCOUNT)
    twofa_lock("acc-keep")
    assert _ACCOUNT in _TWOFA_LOCKS

    await remove_account(_ACCOUNT)

    assert _ACCOUNT not in _TWOFA_LOCKS
    # Keyed by account, not a convenient way to clear the whole table.
    assert "acc-keep" in _TWOFA_LOCKS
