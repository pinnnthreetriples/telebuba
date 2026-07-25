"""Discovery stage 2 — decide which candidates actually accept comments.

No channel catalogue in existence exposes this, so it is resolved from Telegram:
``channelFull.linked_chat_id`` is the signal, and the repo already caches every
verdict in ``neurocomment_linked_groups``. The cache is read in ONE bulk query up
front, which is what makes a repeat search over overlapping keywords nearly free —
only genuinely new (or stale) channels pay an RPC.

Freshness is applied here, not in the repository: onboarding and the board want the
raw cache, but a channel that switched comments on months ago must not stay
filtered out of discovery forever. Same shape as ``services.spam_status._is_fresh``.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING

from core.config import settings
from core.repositories.neurocomment import (
    list_linked_groups,
    list_pending_discovery_candidates,
    mark_discovery_qualified,
    upsert_linked_group,
)
from core.telegram_client import TelegramReadError
from schemas.telegram_actions import GetLinkedDiscussionGroup, LinkedDiscussionGroupResult
from services.neurocomment import _seams
from services.neurocomment._discovery_providers import record_flood
from services.neurocomment._signals import signal_discovery_progress

if TYPE_CHECKING:
    from schemas.neurocomment import LinkedDiscussionGroup

# Emit an SSE nudge every N probes rather than per probe: the stream is debounced
# on the client anyway, and this keeps a 100-candidate run from publishing 100 frames.
_PROGRESS_EVERY = 5


def _is_fresh(checked_at: str, now: datetime) -> bool:
    """Is this cached verdict still trustworthy? A zero TTL falls out as never."""
    try:
        stamped = datetime.fromisoformat(checked_at)
    except ValueError:
        # Text column: a legacy or hand-edited row must re-probe, not raise.
        return False
    if stamped.tzinfo is None:
        stamped = stamped.replace(tzinfo=UTC)
    ttl_hours = settings.neurocomment.discovery_linked_group_ttl_hours
    return stamped + timedelta(hours=ttl_hours) > now


def _fresh_cache(groups: list[LinkedDiscussionGroup], now: datetime) -> set[str]:
    return {group.channel for group in groups if _is_fresh(group.checked_at, now)}


async def run_qualification(campaign_id: str, account_id: str) -> str | None:
    """Probe every unqualified candidate, paced. Returns a failure reason or ``None``.

    A FloodWait aborts the pass immediately and leaves the remainder pending —
    retrying into a rate limit is how a soft limit becomes a hard one, and
    ``qualified_at`` makes the next run resume exactly where this one stopped.
    """
    pending = await list_pending_discovery_candidates(campaign_id)
    if not pending.rows:
        return None

    now = datetime.now(UTC)
    channels = [row.channel for row in pending.rows]
    cached = await list_linked_groups(channels)
    fresh = _fresh_cache(cached.groups, now)

    consecutive_errors = 0
    probed = 0
    for index, row in enumerate(pending.rows):
        if row.channel in fresh:
            # Cache hit: no RPC, and deliberately no sleep — this is what makes a
            # re-search over familiar keywords finish in milliseconds.
            await mark_discovery_qualified(campaign_id, row.channel)
            continue

        if probed:
            await _pace()
        probed += 1
        reason = await _probe_one(campaign_id, account_id, row.channel)
        if reason is not None and "FloodWait" in reason:
            return reason
        if reason is None:
            consecutive_errors = 0
        else:
            consecutive_errors += 1
            if consecutive_errors >= settings.neurocomment.discovery_max_consecutive_errors:
                # A dead session must not burn one RPC per remaining candidate.
                return reason

        if (index + 1) % _PROGRESS_EVERY == 0:
            signal_discovery_progress()

    return None


async def _pace() -> None:
    neuro = settings.neurocomment
    await _seams.sleep(
        _seams.rng.uniform(
            neuro.discovery_qualify_delay_min_seconds,
            neuro.discovery_qualify_delay_max_seconds,
        ),
    )


async def _probe_one(campaign_id: str, account_id: str, channel: str) -> str | None:
    """One comments-enabled probe. Records the attempt either way."""
    try:
        result = await _seams.execute_read(
            account_id,
            GetLinkedDiscussionGroup(channel=channel),
        )
    except TelegramReadError as exc:
        if await record_flood(account_id, exc.reason):
            # A flood wait says nothing about this channel, so leave it unprobed —
            # stamping it would show a permanent "could not check" verdict that only a
            # full re-search clears. Recording the cooldown also keeps the retry off
            # this account until the window closes.
            return exc.reason
        await mark_discovery_qualified(campaign_id, channel, error=exc.reason)
        return exc.reason

    if not isinstance(result, LinkedDiscussionGroupResult):  # pragma: no cover - typed gateway
        await mark_discovery_qualified(campaign_id, channel, error="unexpected_result")
        return "unexpected_result"

    # Refresh the shared cache so every campaign (and onboarding) benefits.
    await upsert_linked_group(
        channel,
        result.linked_chat_id,
        comments_enabled=result.comments_enabled,
    )
    await mark_discovery_qualified(
        campaign_id,
        channel,
        subscribers=result.participants_count,
    )
    return None
