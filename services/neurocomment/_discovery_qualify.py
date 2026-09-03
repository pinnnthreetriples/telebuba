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
are applied to it: a row they refuse is deleted, and the run report counts the drop. The
cache keeps the two facts those filters read (about, the join gate — migration #61), so a
fresh row settles them through the SAME derivation as a probe; a row that never learnt a
fact the active filters need is probed.
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
    is_private_ref,
)
from services.neurocomment._discovery_providers import COOLING_REASON, flood_cooldown
from services.neurocomment._discovery_wave_support import pace
from services.neurocomment._signals import signal_discovery_progress

if TYPE_CHECKING:
    from schemas.neurocomment import LinkedDiscussionGroup
    from schemas.neurocomment_discovery import DiscoveryCandidateRow, DiscoverySearchRequest
    from services.neurocomment._discovery_pool import AccountPool

# Emit an SSE nudge every N probes rather than per probe: the stream is debounced
# on the client anyway, and this keeps a 100-candidate run from publishing 100 frames.
# The filters' deletes are flushed on the same tick, one statement instead of one per row.
_PROGRESS_EVERY = 5

# Probes a pass must spend before its failure RATE means anything. Past that, half of
# them failing aborts the pass — a proportion, never a fixed count, because a re-search
# re-inserts every candidate with ``qualified_at = NULL``: a count would abort at the
# same handle on every retry, so the tail past it could never be qualified however many
# searches the operator spends. Ten dead, private or deleted handles in a hundred is an
# ordinary sweep off a broad keyword search; a session failing as often as it answers over
# twenty probes is broken, and that is the case the pool's consecutive counter cannot see.
_ERROR_RATE_MIN_PROBES = 20


class _Probe(NamedTuple):
    """One probe's failure reason, and the cooldown it earns when it was a rate limit.

    The seconds, not a second reading of ``reason``: the gateway spells the flood family
    four different ways and only one of them contains "FloodWait", so the string test
    this replaces let a premium wait or a slow-mode wait run the pass to its end.
    """

    reason: str | None
    flood_seconds: int | None = None


class _Facts(NamedTuple):
    """The three derived facts the filters and the verdict share, whatever answered."""

    access: str | None
    language: str | None
    category_match: bool | None


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


def _facts(
    row: DiscoveryCandidateRow,
    request: DiscoverySearchRequest,
    *,
    about: str | None,
    join_request: bool | None,
) -> _Facts:
    """Access, language and category match — derived ONCE, for the verdict and the filters.

    The row's ``channel`` is a ref, not always a handle: a private ``id:`` row has no
    username, which is exactly what makes its access ``subscription``.
    """
    username = None if is_private_ref(row.channel) else row.channel
    category = request.category
    return _Facts(
        access=access_of(username, join_request),
        language=detect_language(f"{row.title} {about or ''}"),
        category_match=None if category == "any" else matches(row.title, about, category),
    )


def _is_group(kind: str) -> bool | None:
    """The row's stored kind as the verdict's tri-state: a legacy or blank kind is unknown.

    Not ``kind == "group"``: that read every unrecognised string as a confident "channel",
    and the comments filter then deleted the row on a fact nobody had measured.
    """
    return True if kind == "group" else False if kind == "channel" else None


def _cache_answers(group: LinkedDiscussionGroup, request: DiscoverySearchRequest) -> bool:
    """Does this fresh cache row carry every fact the active filters need?

    A pre-#61 row has ``NULL`` where the about text and the join gate should be — facts
    never learnt, not facts known to be blank — so a filter that reads them re-probes.
    """
    if (request.language != "any" or request.category != "any") and group.about is None:
        return False
    return not (request.access in {"open", "join_request"} and group.join_request is None)


async def _settled_without_probe(
    campaign_id: str,
    row: DiscoveryCandidateRow,
    fresh: dict[str, LinkedDiscussionGroup],
    request: DiscoverySearchRequest,
    rejected: list[str],
) -> bool:
    """Qualify the row from what is already known, if that is enough. No RPC either way."""
    if is_private_ref(row.channel):
        # Nothing can probe it, so the filters read the title alone and access is
        # ``subscription``. ``comments=False`` is an explicit rule, not a measurement: a
        # channel nobody can probe or comment in can never satisfy "has comments", so
        # ``comments=on`` refuses it rather than admitting on unknown.
        about, join_request, comments = None, None, False
    else:
        group = fresh.get(row.channel)
        if group is None or not _cache_answers(group, request):
            return False
        # Cache hit: no RPC, and deliberately no sleep — this is what makes a re-search
        # over familiar keywords finish in milliseconds. Every filter still applies.
        about, join_request, comments = group.about, group.join_request, group.comments_enabled
    facts = _facts(row, request, about=about, join_request=join_request)
    # Recorded on the cache path too: the board lifts access, language and the category
    # match off the verdict, so a row settled without a probe showed all three as unknown
    # — the very facts the filters had just read. The rights flags stay ``None``: nothing
    # measured them this run. ``is_group`` is the row's own kind.
    _discovery_state.record_verdict(
        campaign_id,
        row.channel,
        DiscoveryChannelVerdict(is_group=_is_group(row.kind), **facts._asdict()),
    )
    reason = _admit(row, facts, comments_enabled=comments, request=request)
    await _settle(campaign_id, row.channel, reason, rejected)
    return True


def _admit(
    row: DiscoveryCandidateRow,
    facts: _Facts,
    *,
    comments_enabled: bool | None,
    request: DiscoverySearchRequest,
) -> str | None:
    # A group's comments verdict is structurally False (comments ARE its messages), so it
    # is handed over as unknown: the filter must not delete every group a ``kind=all``
    # search found the moment the operator asks for comments on. Only a row KNOWN to be
    # a channel hands the verdict over — an unknown kind is not a channel by default.
    return admit_at_qualification(
        comments_enabled=comments_enabled if _is_group(row.kind) is False else None,
        access=facts.access,
        language=facts.language,
        category_match=facts.category_match,
        request=request,
    )


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
    fresh = {g.channel: g for g in cached.groups if is_fresh(g.checked_at, now)}
    return await _probe_pending(campaign_id, pool, request, pending.rows, fresh)


async def _probe_pending(
    campaign_id: str,
    pool: AccountPool,
    request: DiscoverySearchRequest,
    rows: list[DiscoveryCandidateRow],
    fresh: dict[str, LinkedDiscussionGroup],
) -> str | None:
    # Rows the filters refused, deleted in batches: at the progress tick and at the end,
    # whichever way the pass ends.
    rejected: list[str] = []
    total_errors = 0
    probed = 0
    try:
        for index, row in enumerate(rows):
            if not await _settled_without_probe(campaign_id, row, fresh, request, rejected):
                if probed:
                    await pace()
                # AFTER the pace sleep, because that sleep is one to two seconds long and
                # a limit landing inside it would otherwise still buy one probe. Uncharged:
                # the per-account wave ceiling is not this pass's bound, the candidate
                # limit is.
                account_id = pool.acquire(charge=False)
                if account_id is None:
                    return COOLING_REASON
                probed += 1
                probe = await _probe_one(campaign_id, account_id, row, request, rejected)
                failed = probe.reason is not None
                if await pool.report(account_id, flood_seconds=probe.flood_seconds, failed=failed):
                    return probe.reason
                if failed:
                    total_errors += 1
                    if probed >= _ERROR_RATE_MIN_PROBES and total_errors * 2 >= probed:
                        # A session failing as often as it answers has stopped saying
                        # anything new — see :data:`_ERROR_RATE_MIN_PROBES`.
                        return probe.reason

            if (index + 1) % _PROGRESS_EVERY == 0:
                await delete_discovery_candidates(campaign_id, rejected)
                rejected.clear()
                signal_discovery_progress()
        return None
    finally:
        await delete_discovery_candidates(campaign_id, rejected)


async def _settle(
    campaign_id: str,
    channel: str,
    reason: str | None,
    rejected: list[str],
    *,
    subscribers: int | None = None,
) -> None:
    """Keep the row as qualified, or queue it for deletion when an operator filter refused it."""
    if reason is None:
        await mark_discovery_qualified(campaign_id, channel, subscribers=subscribers)
        return
    rejected.append(channel)
    _discovery_state.bump_filtered(campaign_id, reason)


def _verdict_of(
    result: LinkedDiscussionGroupResult,
    row: DiscoveryCandidateRow,
    request: DiscoverySearchRequest,
) -> tuple[DiscoveryChannelVerdict, _Facts]:
    """Everything the one ``getFullChannel`` reply says about fitness, carried verbatim.

    Copied field for field rather than folded into a summary: collapsing any of these
    into a bool here would break the tri-state contract
    :class:`schemas.telegram_action_results.LinkedDiscussionGroupResult` states, turning
    an unanswered signal into a confident "no" for every reader downstream. The three
    derived facts (access, language, category match) are derived HERE, once, and handed
    back beside the verdict so the filters read the very values the board will show.
    """
    facts = _facts(row, request, about=result.about, join_request=result.target_join_request)
    verdict = DiscoveryChannelVerdict(
        can_send_messages=result.can_send_messages,
        join_to_send=result.join_to_send,
        join_request=result.join_request,
        group_slowmode_enabled=result.group_slowmode_enabled,
        scam=result.scam,
        fake=result.fake,
        restricted=result.restricted,
        is_group=result.is_group,
        **facts._asdict(),
    )
    return verdict, facts


async def _probe_one(
    campaign_id: str,
    account_id: str,
    row: DiscoveryCandidateRow,
    request: DiscoverySearchRequest,
    rejected: list[str],
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

    # Refresh the shared cache so every campaign (and onboarding) benefits. The two facts
    # the filters read ride along; a blank about is stored as "" so it reads as known,
    # unlike a legacy NULL. The rest of the reply rides the run's in-memory state, the
    # same way per-row provenance does.
    await upsert_linked_group(
        row.channel,
        result.linked_chat_id,
        comments_enabled=result.comments_enabled,
        about=result.about or "",
        join_request=result.target_join_request,
    )
    verdict, facts = _verdict_of(result, row, request)
    _discovery_state.record_verdict(campaign_id, row.channel, verdict)
    reason = _admit(row, facts, comments_enabled=result.comments_enabled, request=request)
    await _settle(campaign_id, row.channel, reason, rejected, subscribers=result.participants_count)
    return _Probe(None)
