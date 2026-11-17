"""Closing an appeal on a persisted @SpamBot ``clean`` (docs/spam-intel.md 5.12, owner decision D4).

Its own tiny module so the evidence layer's store path can call it without importing the
appeal service (which reads verdicts from the evidence layer). Nothing else closes an appeal,
and closing one lifts nothing: the lift of a block stays ``services.spam_block_lift``, called
on the same answer. No Telegram I/O, no send path.
"""

from __future__ import annotations

import logging
from datetime import UTC, datetime

from core.logging import log_event
from core.repositories.spam_appeals import close_open_appeal

logger = logging.getLogger(__name__)

CLOSED_BY_CLEAN = "spambot_clean"


def now() -> datetime:
    """The appeal layer's one clock (filing and closing) — patched by tests."""
    return datetime.now(UTC)


async def close_on_clean(account_id: str, at: datetime | None = None) -> bool:
    """A persisted @SpamBot ``clean`` ends the account's open appeal. Never raises.

    Called from the gateway's store path. Nothing else closes an appeal, and closing one lifts
    nothing: the block's lift is ``services.spam_block_lift``, on the same answer.
    """
    try:
        closed = await close_open_appeal(account_id, closed_at=at or now(), kind=CLOSED_BY_CLEAN)
        if closed:
            await log_event(
                "INFO",
                "spam_intel_appeal_closed",
                account_id=account_id,
                extra={"by": CLOSED_BY_CLEAN},
            )
    except Exception as exc:  # noqa: BLE001 - recording the end must never fail the probe.
        logger.warning("spam appeal not closed: %s", type(exc).__name__)
        return False
    return closed
