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

The pass itself is a no-RPC sweep (``_settled_without_probe``) followed by one
``services.neurocomment._discovery_streams.Job`` per row that still needs a real probe —
paced per account stream, concurrent across the pool, exactly like the search stage.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from typing import TYPE_CHECKING, NamedTuple

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
from services.neurocomment._discovery_providers import COOLING_REASON, flood_cooldown
from services.neurocomment._discovery_qualify_facts import (
    _admit,
    _facts,
    _settle,
    _settled_without_probe,
    is_fresh,
)
from services.neurocomment._discovery_streams import Job, JobResult, Streams

if TYPE_CHECKING:
    from schemas.neurocomment_discovery import DiscoveryCandidateRow, DiscoverySearchRequest
    from services.neurocomment._discovery_pool import AccountPool
    from services.neurocomment._discovery_qualify_facts import _Facts
    from services.neurocomment._discovery_state import WorkTracker

__all__ = ["is_fresh", "run_qualification"]

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
    # The client pool could not connect the account at all — no rate limit, no reply.
    unreachable: bool = False


@dataclass(slots=True)
class _ProbeCounters:
    """The error-rate rule's shared state across every concurrent probe."""

    probed: int = 0
    total_errors: int = 0


@dataclass(slots=True)
class _ProbeState:
    """Mutable state every probe closure shares, bundled to keep the factory's arity down.

    ``last_reason`` is keyed by account, not a single shared slot: a slot overwritten by
    whichever failing probe finishes last could hand back a reason that belongs to an
    account whose failure never actually emptied the pool. ``Streams.stopped_by`` says
    which account's drop produced the stop; this is how its own reason is recovered.
    """

    rejected: list[str]
    counters: _ProbeCounters
    last_reason: dict[str, str]


async def _flush(campaign_id: str, rejected: list[str]) -> None:
    """Delete the rows an operator filter refused, once, however many closures ask.

    Snapshot-then-clear with no ``await`` in between: two probe jobs finishing at once
    may both see the threshold crossed, but only the first actually has rows to send.
    """
    if not rejected:
        return
    batch, rejected[:] = list(rejected), []
    await delete_discovery_candidates(campaign_id, batch)


def _probe_job(
    campaign_id: str,
    row: DiscoveryCandidateRow,
    request: DiscoverySearchRequest,
    state: _ProbeState,
) -> Job:
    """One row's probe, uncharged: the candidate limit bounds this pass, not the wave ceiling."""

    async def run(account_id: str, attempt: int) -> JobResult:
        probe = await _probe_one(campaign_id, account_id, row, request, state.rejected)
        retry = probe.flood_seconds is not None or probe.unreachable
        if retry and attempt == 0:
            # A rate limit or a dead client says nothing about this row — worth one
            # try on whichever account picks the job up next, before counting it. Still
            # recorded against THIS account: the pool can empty right here, with no
            # account left to run that retry at all.
            if probe.reason is not None:
                state.last_reason[account_id] = probe.reason
            return JobResult(
                flood_seconds=probe.flood_seconds,
                error=probe.reason,
                retry=True,
                unreachable=probe.unreachable,
            )
        state.counters.probed += 1
        failed = probe.reason is not None
        abort = None
        if failed:
            state.last_reason[account_id] = probe.reason
            state.counters.total_errors += 1
            probed, errors = state.counters.probed, state.counters.total_errors
            if probed >= _ERROR_RATE_MIN_PROBES and errors * 2 >= probed:
                # A session failing as often as it answers has stopped saying anything
                # new — see :data:`_ERROR_RATE_MIN_PROBES`.
                abort = probe.reason
        if len(state.rejected) >= _PROGRESS_EVERY:
            await _flush(campaign_id, state.rejected)
        return JobResult(
            flood_seconds=probe.flood_seconds,
            failed=failed,
            abort=abort,
            error=probe.reason,
            unreachable=probe.unreachable,
        )

    # ``source`` only matters to a wave's own ``truncated()`` report, which this
    # uncharged pass never calls — any of the four literals is a safe placeholder.
    return Job(source="telegram_search", run=run, order=0, charge=False)


async def run_qualification(
    campaign_id: str,
    pool: AccountPool,
    request: DiscoverySearchRequest,
    work: WorkTracker,
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

    # First pass, sequential and RPC-free: whatever the cache (or an unprobeable
    # private ref) already settles needs no stream at all.
    rejected: list[str] = []
    to_probe = [
        row
        for row in pending.rows
        if not await _settled_without_probe(campaign_id, row, fresh, request, rejected)
    ]
    await _flush(campaign_id, rejected)

    work.extra = 0
    state = _ProbeState(rejected=rejected, counters=_ProbeCounters(), last_reason={})
    jobs = [_probe_job(campaign_id, row, request, state) for row in to_probe]
    streams = Streams(pool, work, signal_every=_PROGRESS_EVERY)
    try:
        stop = await streams.run(jobs)
    finally:
        await _flush(campaign_id, rejected)

    if stop is None:
        return None
    if stop == "cooling":
        return COOLING_REASON
    if streams.stopped_by is not None:
        # The account whose drop produced the stop, not whichever probe merely
        # finished last — see ``_ProbeState.last_reason``.
        return state.last_reason.get(streams.stopped_by, stop)
    # No one account's drop caused this: the abort rule tripped, and ``stop`` is
    # already that rule's own reason text.
    return stop


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
        if exc.kind == "unavailable":
            # The client pool never reached Telegram, so this says nothing about the
            # channel either — same treatment as a rate limit, minus the cooldown.
            return _Probe(exc.reason, unreachable=True)
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
