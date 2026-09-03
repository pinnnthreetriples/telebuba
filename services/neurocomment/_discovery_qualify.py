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
mode, Telegram's scam/fake/restricted marks, the about text — so the whole verdict is
kept (in memory, see ``_discovery_state``) instead of being thrown away for the sake of
one bool, and the operator's probe-time filters (comments, access, language, category)
are applied to it: a row they refuse is deleted, and the run report counts the drop.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING, NamedTuple

from core.config import settings
from core.repositories.neurocomment import (
    delete_discovery_candidates,
    list_linked_groups,
    list_pending_discovery_candidates,
    mark_discovery_qualified,
    upsert_linked_group,
)
from core.telegram_client import TelegramReadError
from schemas.neurocomment_discovery import DiscoveryChannelVerdict
from schemas.telegram_actions import GetLinkedDiscussionGroup, LinkedDiscussionGroupResult
from services.neurocomment import _discovery_state, _seams
from services.neurocomment._discovery_categories import matches
from services.neurocomment._discovery_filters import (
    access_of,
    admit_at_qualification,
    detect_language,
)
from services.neurocomment._discovery_providers import COOLING_REASON, flood_cooldown
from services.neurocomment._signals import signal_discovery_progress

if TYPE_CHECKING:
    from schemas.neurocomment import LinkedDiscussionGroup
    from schemas.neurocomment_discovery import DiscoveryCandidateRow, DiscoverySearchRequest
    from services.neurocomment._discovery_pool import AccountPool

# Emit an SSE nudge every N probes rather than per probe: the stream is debounced
# on the client anyway, and this keeps a 100-candidate run from publishing 100 frames.
_PROGRESS_EVERY = 5

# Probes a pass must spend before its failure RATE means anything. Past that, half of
# them failing aborts the pass — a proportion, never a fixed count, because a re-search
# re-inserts every candidate with ``qualified_at = NULL``: a count would abort at the
# same handle on every retry, so the tail past it could never be qualified however many
# searches the operator spends. Ten dead, private or deleted handles in a hundred is an
# ordinary sweep off a broad keyword search; a session failing as often as it answers over
# twenty probes is broken, and that is the case the pool's consecutive counter cannot see.
_ERROR_RATE_MIN_PROBES = 20

# Rows stored under ``id:<n>``: a channel with no public handle, which nothing can probe
# and no campaign can comment in. Qualified as-is with this verdict.
PRIVATE_PREFIX = "id:"


class _Probe(NamedTuple):
    """One probe's failure reason, and the cooldown it earns when it was a rate limit.

    The seconds, not a second reading of ``reason``: the gateway spells the flood family
    four different ways and only one of them contains "FloodWait", so the string test
    this replaces let a premium wait or a slow-mode wait run the pass to its end.
    """

    reason: str | None
    flood_seconds: int | None = None


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


def _fresh_cache(
    groups: list[LinkedDiscussionGroup],
    now: datetime,
    request: DiscoverySearchRequest,
) -> dict[str, bool]:
    """Channel -> cached comments verdict, for the rows the cache still answers.

    The cache answers comments on/off only. A language or category filter needs the
    ``about`` text, which only the probe carries, so those requests get no shortcut.
    """
    if request.language != "any" or request.category != "any":
        return {}
    return {
        group.channel: group.comments_enabled for group in groups if is_fresh(group.checked_at, now)
    }


async def _settled_without_probe(
    campaign_id: str,
    row: DiscoveryCandidateRow,
    fresh: dict[str, bool],
    request: DiscoverySearchRequest,
) -> bool:
    """Qualify the row from what is already known, if that is enough. No RPC either way."""
    if row.channel.startswith(PRIVATE_PREFIX):
        verdict = DiscoveryChannelVerdict(access="subscription")
        _discovery_state.record_verdict(campaign_id, row.channel, verdict)
        await mark_discovery_qualified(campaign_id, row.channel)
        return True
    if row.channel not in fresh:
        return False
    # Cache hit: no RPC, and deliberately no sleep — this is what makes a re-search over
    # familiar keywords finish in milliseconds. The comments filter still applies.
    reason = admit_at_qualification(
        title=row.title,
        about=None,
        comments_enabled=fresh[row.channel],
        access=None,
        request=request,
    )
    await _settle(campaign_id, row.channel, reason)
    return True


async def run_qualification(
    campaign_id: str,
    pool: AccountPool,
    request: DiscoverySearchRequest,
) -> str | None:
    """Probe every unqualified candidate, paced. Returns a failure reason or ``None``.

    A rate limit parks the account it landed on and the pass goes on with the rest of
    the pool; when none is left it stops and leaves the remainder pending — retrying into
    a rate limit is how a soft limit becomes a hard one, and ``qualified_at`` makes the
    next run resume exactly where this one stopped. That holds for a limit this pass did
    not cause, too: the pool re-reads every account's cooldown before handing it out.
    """
    pending = await list_pending_discovery_candidates(campaign_id)
    if not pending.rows:
        return None

    now = datetime.now(UTC)
    cached = await list_linked_groups([row.channel for row in pending.rows])
    fresh = _fresh_cache(cached.groups, now, request)

    total_errors = 0
    probed = 0
    for index, row in enumerate(pending.rows):
        if await _settled_without_probe(campaign_id, row, fresh, request):
            continue

        if probed:
            await _pace()
        # AFTER the pace sleep, because that sleep is one to two seconds long and a limit
        # landing inside it would otherwise still buy one probe.
        account_id = pool.acquire()
        if account_id is None:
            return COOLING_REASON
        probed += 1
        probe = await _probe_one(campaign_id, account_id, row, request)
        failed = probe.reason is not None
        if await pool.report(account_id, flood_seconds=probe.flood_seconds, failed=failed):
            return probe.reason
        if failed:
            total_errors += 1
            if probed >= _ERROR_RATE_MIN_PROBES and total_errors * 2 >= probed:
                # A session failing as often as it answers has stopped saying anything
                # new about the candidates — see :data:`_ERROR_RATE_MIN_PROBES`.
                return probe.reason

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


async def _settle(
    campaign_id: str,
    channel: str,
    reason: str | None,
    *,
    subscribers: int | None = None,
) -> None:
    """Keep the row as qualified, or drop it when an operator filter refused it."""
    if reason is None:
        await mark_discovery_qualified(campaign_id, channel, subscribers=subscribers)
        return
    await delete_discovery_candidates(campaign_id, [channel])
    _discovery_state.bump_filtered(campaign_id, reason)


def _verdict_of(
    result: LinkedDiscussionGroupResult,
    row: DiscoveryCandidateRow,
    request: DiscoverySearchRequest,
) -> DiscoveryChannelVerdict:
    """Everything the one ``getFullChannel`` reply says about fitness, carried verbatim.

    Copied field for field rather than folded into a summary: collapsing any of these
    into a bool here would break the tri-state contract
    :class:`schemas.telegram_action_results.LinkedDiscussionGroupResult` states, turning
    an unanswered signal into a confident "no" for every reader downstream. The three
    derived facts (access, language, category match) are derived HERE because the
    about text they need is not persisted.
    """
    category = request.category
    return DiscoveryChannelVerdict(
        can_send_messages=result.can_send_messages,
        join_to_send=result.join_to_send,
        join_request=result.join_request,
        group_slowmode_enabled=result.group_slowmode_enabled,
        scam=result.scam,
        fake=result.fake,
        restricted=result.restricted,
        access=access_of(row.channel, result.target_join_request),
        language=detect_language(f"{row.title} {result.about or ''}"),
        is_group=result.is_group,
        category_match=None if category == "any" else matches(row.title, result.about, category),
    )


async def _probe_one(
    campaign_id: str,
    account_id: str,
    row: DiscoveryCandidateRow,
    request: DiscoverySearchRequest,
) -> _Probe:
    """One comments-enabled probe. Records the attempt either way — except a rate limit."""
    try:
        result = await _seams.execute_read(
            account_id,
            GetLinkedDiscussionGroup(channel=row.channel),
        )
    except TelegramReadError as exc:
        seconds = flood_cooldown(exc)
        if seconds is not None:
            # A rate limit says nothing about this channel, so leave it unprobed —
            # stamping it would show a permanent "could not check" verdict that only a
            # full re-search clears. The pool records the cooldown, which also keeps the
            # retry off this account until the window closes.
            return _Probe(exc.reason, flood_seconds=seconds)
        await mark_discovery_qualified(campaign_id, row.channel, error=exc.reason)
        return _Probe(exc.reason)

    if not isinstance(result, LinkedDiscussionGroupResult):  # pragma: no cover - typed gateway
        await mark_discovery_qualified(campaign_id, row.channel, error="unexpected_result")
        return _Probe("unexpected_result")

    # Refresh the shared cache so every campaign (and onboarding) benefits. Only the
    # comments verdict has a column there; the rest of the reply rides the run's
    # in-memory state, the same way per-row provenance does.
    await upsert_linked_group(
        row.channel,
        result.linked_chat_id,
        comments_enabled=result.comments_enabled,
    )
    verdict = _verdict_of(result, row, request)
    _discovery_state.record_verdict(campaign_id, row.channel, verdict)
    reason = admit_at_qualification(
        title=row.title,
        about=result.about,
        comments_enabled=result.comments_enabled,
        access=verdict.access,
        request=request,
    )
    await _settle(campaign_id, row.channel, reason, subscribers=result.participants_count)
    return _Probe(None)
