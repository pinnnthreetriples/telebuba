"""Discovery stage 2 — decide which candidates this campaign can actually comment in.

No channel catalogue in existence exposes this, so it is resolved from Telegram:
``channelFull.linked_chat_id`` is the signal, and the repo already caches every
verdict in ``neurocomment_linked_groups``. The cache is read in ONE bulk query up
front, which is what makes a repeat search over overlapping keywords nearly free —
only genuinely new (or stale) channels pay an RPC.

Freshness is applied here, not in the repository: onboarding and the board want the
raw cache, but a channel that switched comments on months ago must not stay
filtered out of discovery forever. Same shape as ``services.spam_status._is_fresh``; it
is module-public here because the adopt guard has to apply the identical window.

That one reply answers more than comments on/off — writing rights, the join gates, slow
mode, Telegram's scam/fake/restricted marks — so the whole verdict is kept (in memory,
see ``_discovery_state``) instead of being thrown away for the sake of one bool.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING, NamedTuple

from core.config import settings
from core.repositories.neurocomment import (
    list_linked_groups,
    list_pending_discovery_candidates,
    mark_discovery_qualified,
    upsert_linked_group,
)
from core.telegram_client import TelegramReadError
from schemas.neurocomment_discovery import DiscoveryChannelVerdict
from schemas.telegram_actions import GetLinkedDiscussionGroup, LinkedDiscussionGroupResult
from services.neurocomment import _discovery_state, _seams
from services.neurocomment._discovery_providers import (
    COOLING_REASON,
    account_cooling,
    flood_cooldown,
    record_flood,
)
from services.neurocomment._signals import signal_discovery_progress

if TYPE_CHECKING:
    from schemas.neurocomment import LinkedDiscussionGroup

# Emit an SSE nudge every N probes rather than per probe: the stream is debounced
# on the client anyway, and this keeps a 100-candidate run from publishing 100 frames.
_PROGRESS_EVERY = 5

# Probes a pass must spend before its failure RATE means anything. Past that, half of
# them failing aborts the pass — a proportion, never a fixed count, because a re-search
# re-inserts every candidate with ``qualified_at = NULL``: a count would abort at the
# same handle on every retry, so the tail past it could never be qualified however many
# searches the operator spends. Ten dead, private or deleted handles in a hundred is an
# ordinary sweep off a broad keyword search; a session failing as often as it answers over
# twenty probes is broken, and that is the case the consecutive counter cannot see.
_ERROR_RATE_MIN_PROBES = 20


class _Probe(NamedTuple):
    """One probe's failure reason, and whether it was a rate limit that ends the pass.

    The flag, not a second reading of ``reason``: the gateway spells the flood family
    four different ways and only one of them contains "FloodWait", so the string test
    this replaces let a premium wait or a slow-mode wait run the pass to its end.
    """

    reason: str | None
    flooded: bool = False


def is_fresh(checked_at: str, now: datetime) -> bool:
    """Is this cached verdict still trustworthy? A zero TTL falls out as never.

    Module-public: the adopt guard in ``discovery`` must apply the SAME window as this
    probe loop, and reaching across a module boundary for a private name to do it said
    the opposite.
    """
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
    return {group.channel for group in groups if is_fresh(group.checked_at, now)}


async def run_qualification(campaign_id: str, account_id: str) -> str | None:
    """Probe every unqualified candidate, paced. Returns a failure reason or ``None``.

    A rate limit aborts the pass immediately and leaves the remainder pending —
    retrying into a rate limit is how a soft limit becomes a hard one, and
    ``qualified_at`` makes the next run resume exactly where this one stopped. That
    holds for a limit this pass did not cause, too: the account's cooldown is re-read
    before every probe, so a flood recorded elsewhere stops the pass at the next read
    rather than at its last one.
    """
    pending = await list_pending_discovery_candidates(campaign_id)
    if not pending.rows:
        return None

    now = datetime.now(UTC)
    channels = [row.channel for row in pending.rows]
    cached = await list_linked_groups(channels)
    fresh = _fresh_cache(cached.groups, now)

    consecutive_errors = 0
    total_errors = 0
    probed = 0
    for index, row in enumerate(pending.rows):
        if row.channel in fresh:
            # Cache hit: no RPC, and deliberately no sleep — this is what makes a
            # re-search over familiar keywords finish in milliseconds.
            await mark_discovery_qualified(campaign_id, row.channel)
            continue

        if probed:
            await _pace()
        if account_cooling(account_id):
            # Re-read before every RPC this pass is about to spend, not once at the
            # start: a hundred probes is minutes, and a limit the comment engine (or
            # this run's own search stage) recorded meanwhile is invisible to the two
            # error counters below. AFTER the pace sleep, because that sleep is one to
            # two seconds long and a limit landing inside it would otherwise still buy
            # one probe. Stops the same way a FloodWait does — the remainder stays
            # pending, ``qualified_at`` resumes the next run where this one left off —
            # and reports why rather than returning ``None`` for "finished".
            return COOLING_REASON
        probed += 1
        probe = await _probe_one(campaign_id, account_id, row.channel)
        if probe.flooded:
            return probe.reason
        reason = probe.reason
        if reason is None:
            consecutive_errors = 0
        else:
            consecutive_errors += 1
            total_errors += 1
            if _dead_session(consecutive_errors, probed, total_errors):
                return reason

        if (index + 1) % _PROGRESS_EVERY == 0:
            signal_discovery_progress()

    return None


def _dead_session(consecutive: int, probed: int, total_errors: int) -> bool:
    """Have this pass's failures stopped saying anything new about the candidates?

    Two rules, both on the SESSION rather than on any one channel: a dead one must not
    burn one RPC per remaining candidate, and a half-dead one never trips the
    consecutive counter at all — see :data:`_ERROR_RATE_MIN_PROBES` for why the second
    is a proportion and not a count.
    """
    if consecutive >= settings.neurocomment.discovery_max_consecutive_errors:
        return True
    return probed >= _ERROR_RATE_MIN_PROBES and total_errors * 2 >= probed


async def _pace() -> None:
    neuro = settings.neurocomment
    await _seams.sleep(
        _seams.rng.uniform(
            neuro.discovery_qualify_delay_min_seconds,
            neuro.discovery_qualify_delay_max_seconds,
        ),
    )


def _verdict_of(result: LinkedDiscussionGroupResult) -> DiscoveryChannelVerdict:
    """Everything the one ``getFullChannel`` reply says about fitness, carried verbatim.

    Copied field for field rather than folded into a summary: collapsing any of these
    into a bool here would break the tri-state contract
    :class:`schemas.telegram_action_results.LinkedDiscussionGroupResult` states, turning
    an unanswered signal into a confident "no" for every reader downstream.
    """
    return DiscoveryChannelVerdict(
        can_send_messages=result.can_send_messages,
        join_to_send=result.join_to_send,
        join_request=result.join_request,
        group_slowmode_enabled=result.group_slowmode_enabled,
        scam=result.scam,
        fake=result.fake,
        restricted=result.restricted,
    )


async def _probe_one(campaign_id: str, account_id: str, channel: str) -> _Probe:
    """One comments-enabled probe. Records the attempt either way."""
    try:
        result = await _seams.execute_read(
            account_id,
            GetLinkedDiscussionGroup(channel=channel),
        )
    except TelegramReadError as exc:
        if await record_flood(account_id, flood_cooldown(exc)):
            # A rate limit says nothing about this channel, so leave it unprobed —
            # stamping it would show a permanent "could not check" verdict that only a
            # full re-search clears. Recording the cooldown also keeps the retry off
            # this account until the window closes.
            return _Probe(exc.reason, flooded=True)
        await mark_discovery_qualified(campaign_id, channel, error=exc.reason)
        return _Probe(exc.reason)

    if not isinstance(result, LinkedDiscussionGroupResult):  # pragma: no cover - typed gateway
        await mark_discovery_qualified(campaign_id, channel, error="unexpected_result")
        return _Probe("unexpected_result")

    # Refresh the shared cache so every campaign (and onboarding) benefits. Only the
    # comments verdict has a column there; the rest of the reply rides the run's
    # in-memory state, the same way per-row provenance does.
    await upsert_linked_group(
        channel,
        result.linked_chat_id,
        comments_enabled=result.comments_enabled,
    )
    _discovery_state.record_verdict(campaign_id, channel, _verdict_of(result))
    await mark_discovery_qualified(
        campaign_id,
        channel,
        subscribers=result.participants_count,
    )
    return _Probe(None)
