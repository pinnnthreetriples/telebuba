"""Verdicts that are not an @SpamBot reading but a finding another feature made itself.

Masstagging writes ``limited`` when a campaign account is halted by a spam block (PeerFlood).
That write used to go straight to the table, past the gateway: this is the same row, now
through the one door, and the same finding is also recorded as ``peer_flood_send`` evidence (the
fold reads a legacy ``peer_flood`` row as that kind too, so it never looks like a fresh @SpamBot
reading; ``docs/spam-intel.md``).
"""

from __future__ import annotations

from core.repositories.spam_status import upsert_spam_status
from schemas.spam_status import SpamStatusVerdict
from services.spam_intel._evidence import keep_prior_reading, record_evidence


async def record_peer_flood_limited(account_id: str, *, checked_at: str) -> SpamStatusVerdict:
    """Store ``limited`` (detail ``peer_flood``) for an account a feature saw PeerFlooded."""
    await keep_prior_reading(account_id)  # the reading this row replaces stays evidence
    stored = await upsert_spam_status(
        SpamStatusVerdict(
            account_id=account_id,
            status="limited",
            detail="peer_flood",
            checked_at=checked_at,
        )
    )
    # After the row, so the projection is folded later than the reading it repeats.
    await record_evidence(
        account_id, "peer_flood_send", source="masstagging", operation="masstag", detail="PeerFlood"
    )
    return stored
