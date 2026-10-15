"""A bulk message run's hold on its accounts in ``services._account_owner``.

A run claims the accounts it sends from when its loop starts — the first start and every
resume after a restart — the way ``services.chat_broadcast._runtime.claim_free`` does:
each account's lifecycle lock is held across the reads and the claim, so a warming,
listener or campaign start serialises with it per account. Holding the claim is what
makes the other features see the account as busy: warming, the listener, the comment
engine and the other campaigns refuse or skip it, and warming's channel search takes it
only with the operator's consent.

An account another feature is driving is LEFT OUT of the run rather than waited for —
such a hold can last days. A warming account is never taken. The runner reports each
left-out account's recipients as ``account_busy``.

Every send goes out under the same lifecycle lock, so a channel search the operator
pulled the account into pauses the run's next send from it instead of sharing the
session. The run releases its claims when its loop ends, however it ends.
"""

from __future__ import annotations

from contextlib import AsyncExitStack
from typing import TYPE_CHECKING

from core.db import list_warming_account_ids
from core.repositories.neurocomment import list_active_campaign_account_names
from services import _account_owner

if TYPE_CHECKING:
    from collections.abc import Awaitable, Callable

OWNER: _account_owner.Owner = "bulk_messages"
# The error code a left-out account's recipients finish with.
BUSY_CODE = "account_busy"


async def claim_accounts(run_id: str, account_ids: list[str]) -> list[str]:
    """Claim every account no other feature is driving; return the ones left out.

    Idempotent for the run's own claims, so a resume re-takes what it held. The locks
    are taken in sorted order, the order every other multi-account claim takes them in.
    """
    from services.neurocomment import _discovery_state  # noqa: PLC0415 - import cycle
    from services.neuroshilling._listener_lookup import (  # noqa: PLC0415 - import cycle
        running_listener_account_id,
    )
    from services.warming import account_lock  # noqa: PLC0415 - import cycle

    # Warming's channel search holds a lent account's lock for minutes: leave it out, as
    # every other feature's start does, rather than wait the search out.
    lent = {a for a in account_ids if _account_owner.is_lent(a)}
    refused = set(lent)
    async with AsyncExitStack() as locks:
        for account_id in sorted(set(account_ids) - lent):
            await locks.enter_async_context(account_lock(account_id))
        busy = set(await list_active_campaign_account_names())
        busy |= set(await list_warming_account_ids())
        if (listener := await running_listener_account_id()) is not None:
            busy.add(listener)
        for account_id in sorted(set(account_ids) - lent):
            if (
                account_id in busy
                or _discovery_state.account_busy(account_id)
                or _account_owner.try_claim(account_id, OWNER, run_id) is not None
            ):
                refused.add(account_id)
    return [account_id for account_id in account_ids if account_id in refused]


def release_accounts(run_id: str) -> None:
    """Give back every account this run holds, and only the ones it holds."""
    for account_id, owner in _account_owner.owners().items():
        if owner == OWNER and _account_owner.holder_of(account_id) == run_id:
            _account_owner.release(account_id, OWNER, run_id)


async def send_locked[T](account_id: str, send: Callable[[], Awaitable[T]]) -> T:
    """Run one send under the account's lifecycle lock."""
    from services.warming import account_lock  # noqa: PLC0415 - import cycle

    async with account_lock(account_id):
        return await send()
