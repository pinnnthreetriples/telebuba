"""The rolling-24h join budget a campaign spends, and the answer when it is gone.

Split out of :mod:`services.neuroshilling._telegram` to keep that module inside the
file-size gate; ``_telegram`` imports both names back, so ``_telegram.at_join_cap``
still resolves. The counter belongs to neurocomment's table on purpose — see
:func:`at_join_cap` — and ``services._join_lock`` is what serialises reading it
against charging it.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING

from core.config import settings
from core.logging import log_event
from core.repositories.neurocomment import count_account_joins_since
from services._account_limits import account_join_cap

if TYPE_CHECKING:
    from schemas.neuroshilling import NeuroshillingPresenceState


async def at_join_cap(account_id: str) -> bool:
    """True when ``account_id`` has spent its rolling-24h join budget (0 = no cap).

    Counted out of ``neurocomment_join_log``, the SAME table neurocomment gates on,
    and that is deliberate: Telegram freezes an account somewhere north of 20-50
    joins a day and does not care which of our features spent them. A private
    counter would have let one account join forty times with both features certain
    they had stayed under twenty. The price is that neurocomment reaches its own cap
    sooner when a campaign is running, which is the point rather than a side effect.

    A per-account override governs BOTH features for the same reason the counter
    does: it is one account's join budget, not one feature's. Only the fleet default
    below differs between the two.
    """
    cap = await account_join_cap(account_id, settings.neuroshilling.max_joins_per_account_per_day)
    if cap <= 0:
        return False
    since = (datetime.now(UTC) - timedelta(days=1)).isoformat()
    return await count_account_joins_since(account_id, since) >= cap


async def daily_cap_reached(account_id: str, target: str) -> NeuroshillingPresenceState:
    """Log the refusal and leave the pair as it was.

    ``pending`` without touching the stored row: nothing was learnt about the pair,
    and overwriting a previous refusal with "pending" would erase the only record of
    why it failed.
    """
    await log_event(
        "WARNING",
        "neuroshilling_join_daily_cap",
        account_id=account_id,
        extra={"target": target},
    )
    return "pending"
