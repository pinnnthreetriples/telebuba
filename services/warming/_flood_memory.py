"""Best-effort write of one PeerFlood into the account's lifetime flood history."""

from __future__ import annotations

import logging

from core.repositories.warming_floods import record_warming_flood_event
from schemas.warming_flood import WarmingFloodEventWrite
from services.warming.pacing import _now_iso

logger = logging.getLogger(__name__)


async def remember_flood(account_id: str, action: str | None) -> None:
    """Record the flood; a failed write is logged, never raised.

    The caller is about to write the peer-flood → quarantine transition, which matters
    more than the history row: an ordinary DB fault here must not turn it into an error
    state. Cancellation is not an ``Exception`` and still propagates.
    """
    try:
        await record_warming_flood_event(
            WarmingFloodEventWrite(account_id=account_id, created_at=_now_iso(), action=action),
        )
    except Exception as exc:  # noqa: BLE001 - history must not block the safety transition.
        logger.warning(
            "warming flood history write failed (account=%s): %s",
            account_id,
            type(exc).__name__,
            exc_info=exc,
        )
