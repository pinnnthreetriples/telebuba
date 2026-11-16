"""Verdicts that are not an @SpamBot reading but a finding another feature made itself.

Masstagging writes ``limited`` when a campaign account is halted by a spam block (PeerFlood).
That write used to go straight to the table, past the gateway: this is the same row, now
through the one door. In Stage 2 it becomes ``peer_flood_send`` evidence and stops looking like
a fresh @SpamBot reading (``docs/spam-intel.md``).
"""

from __future__ import annotations

from core.repositories.spam_status import upsert_spam_status
from schemas.spam_status import SpamStatusVerdict


async def record_peer_flood_limited(account_id: str, *, checked_at: str) -> SpamStatusVerdict:
    """Store ``limited`` (detail ``peer_flood``) for an account a feature saw PeerFlooded."""
    return await upsert_spam_status(
        SpamStatusVerdict(
            account_id=account_id,
            status="limited",
            detail="peer_flood",
            checked_at=checked_at,
        )
    )
