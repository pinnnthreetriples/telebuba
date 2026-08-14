"""Discovery stage 1 — merge what the sources returned and persist the candidates.

The Telegram half (every wave and the read budget they share) lives in
``_discovery_waves``; this module is the pure one, and the only one that writes.

A wave the run's read budget stopped reports itself truncated, which is NOT a run
error; a FloodWait ends every later wave AND stops the run replacing the stored
candidates with its partial findings.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from core.channel_tokens import dedup_key, normalize_channel
from core.config import settings
from core.repositories.neurocomment import replace_discovery_candidates
from schemas.neurocomment_discovery import (
    CHANNEL_HANDLE_MAX_LENGTH,
    DiscoveryCandidateOrigin,
    DiscoveryCandidateRow,
    DiscoveryRunReport,
    DiscoverySearchStageResult,
    DiscoverySourceReport,
)
from services.neurocomment._discovery_waves import READ_BUDGET, SOURCE_PRIORITY, native_pass

if TYPE_CHECKING:
    from schemas.neurocomment_discovery import DiscoverySearchRequest, DiscoverySourceState
    from services.neurocomment._discovery_providers import RawCandidate, SourceOutcome


def _within_member_bounds(subscribers: int | None, request: DiscoverySearchRequest) -> bool:
    """Apply the subscriber filter to hits whose count we happen to know.

    Native search usually returns no count at all, and an unknown count must not be
    silently dropped — qualification backfills it from the probe when it has to make
    one, and the operator can see it then.
    """
    if subscribers is None:
        return True
    if request.members_min is not None and subscribers < request.members_min:
        return False
    return not (request.members_max is not None and subscribers > request.members_max)


def _normalized(
    ranked: list[tuple[int, RawCandidate]],
) -> list[tuple[str, str, int, RawCandidate]]:
    """Ranked hits paired with their dedup key, canonical handle and outcome, unusable dropped.

    The outcome index rides along because the interleave below shares the cap between
    outcomes, not between sources: one counter per source made rank a position in the
    concatenation of every keyword's hits, so keyword 0 filled the cap and the rest of
    the sweep was paid for and thrown away.
    """
    entries: list[tuple[str, str, int, RawCandidate]] = []
    for group, candidate in ranked:
        handle = normalize_channel(candidate.username, max_length=CHANNEL_HANDLE_MAX_LENGTH)
        if handle is None or handle.startswith("+"):
            # Invite-only links have no public handle to search or comment under.
            continue
        entries.append((dedup_key(handle), handle, group, candidate))
    return entries


def _source_reports(
    outcomes: list[SourceOutcome],
    origins: dict[str, DiscoveryCandidateOrigin],
) -> list[DiscoverySourceReport]:
    """One report per source considered, in priority order."""
    reports: list[DiscoverySourceReport] = []
    for source in SOURCE_PRIORITY:
        own = [outcome for outcome in outcomes if outcome.source == source]
        if not own:
            continue
        state: DiscoverySourceState = "skipped"
        if any(outcome.answered for outcome in own):
            # Partial: one keyword failing while another answered is a degraded run, not
            # a dead source, and the reason below still names it.
            state = "ran"
        elif any(outcome.state == "failed" for outcome in own):
            state = "failed"
        degraded = next((outcome for outcome in own if outcome.error), None)
        reports.append(
            DiscoverySourceReport(
                source=source,
                state=state,
                hits=sum(len(outcome.candidates) for outcome in own),
                kept=sum(1 for origin in origins.values() if source in origin.sources),
                exclusive=sum(1 for origin in origins.values() if origin.sources == [source]),
                reason=None if degraded is None else degraded.error,
                truncated=any(outcome.truncated for outcome in own),
            ),
        )
    return reports


def _merge(
    outcomes: list[SourceOutcome],
    request: DiscoverySearchRequest,
) -> tuple[list[DiscoveryCandidateRow], str | None, DiscoveryRunReport]:
    """Normalize, dedup, interleave and cap the union of every source's hits."""
    ranked: list[tuple[int, RawCandidate]] = []
    for group, outcome in enumerate(outcomes):
        ranked.extend((group, candidate) for candidate in outcome.candidates)
    # Stable sort by source priority so the dedup below keeps the preferred spelling.
    ranked.sort(key=lambda pair: SOURCE_PRIORITY.get(pair[1].source, 99))
    entries = _normalized(ranked)

    # Pool the subscriber counts before deduping: a hit that carries no count can
    # outrank one that does, and would otherwise shadow the very count that decides
    # whether the channel passes the member filter at all.
    counts: dict[str, int] = {}
    for key, _handle, _group, candidate in entries:
        if candidate.subscribers is not None:
            counts.setdefault(key, candidate.subscribers)

    accepted: dict[str, DiscoveryCandidateRow] = {}
    origins: dict[str, DiscoveryCandidateOrigin] = {}
    # Each channel's best position within any one outcome's own result list.
    rank: dict[str, int] = {}
    seen_per_group: dict[int, int] = {}
    for key, handle, group, candidate in entries:
        if key not in accepted:
            subscribers = counts.get(key)
            # A pure function of the pooled count, which is complete before this loop, so
            # a revisited key recomputes the same verdict — no need to memoise a rejection.
            if not _within_member_bounds(subscribers, request):
                continue
            accepted[key] = DiscoveryCandidateRow(
                channel=handle,
                title=candidate.title,
                subscribers=subscribers,
                source=candidate.source,
            )
            origins[key] = DiscoveryCandidateOrigin()
        origin = origins[key]
        if candidate.source in origin.sources:
            continue
        origin.sources.append(candidate.source)
        within = seen_per_group.get(group, 0)
        seen_per_group[group] = within + 1
        rank[key] = min(rank.get(key, within), within)

    # Interleave: each outcome's Nth hit, THEN the priority tiebreak. Priority governs the
    # dedup above (canonical spelling) and must not govern truncation as well, or the
    # lowest-priority source sits permanently at the tail and a sweep that fills the cap
    # drops every row it found. Per OUTCOME, not per source, so the cap is shared across
    # keywords as well as sources.
    selected = sorted(
        accepted,
        key=lambda key: (rank[key], SOURCE_PRIORITY.get(accepted[key].source, 99)),
    )[: settings.neurocomment.discovery_max_candidates]
    kept_origins = {accepted[key].channel: origins[key] for key in selected}

    # First error wins: the board shows one short reason, not a concatenation. The
    # per-source report carries the rest. The read budget is NOT one of them: at default
    # settings a full keyword list exhausts it on every run, so painting a complete answer
    # as a degraded one made the normal case look broken. It is truncation — the source's
    # own row still names the budget and flags itself ``truncated``.
    error = next(
        (out.error for out in outcomes if out.error and out.error != READ_BUDGET),
        None,
    )
    report = DiscoveryRunReport(
        sources=_source_reports(outcomes, kept_origins),
        origins=kept_origins,
    )
    return [accepted[key] for key in selected], error, report


async def run_search(
    campaign_id: str,
    account_id: str,
    request: DiscoverySearchRequest,
) -> DiscoverySearchStageResult:
    """Collect candidates from every enabled source and persist the merged set.

    A source that fails is recorded, never raised: the other source's results still have
    value to the operator.
    """
    native = await native_pass(account_id, request)
    outcomes = native.outcomes

    rows, error, report = _merge(outcomes, request)
    # The write is delete-then-insert, so an empty merge nobody answered for would destroy
    # the previous run's already-qualified candidates over one transient failure. An empty
    # merge now also needs the KEYWORD SWEEP to have answered: the wider waves are
    # consulted on every run, and letting one of them answer "nothing" for a sweep that
    # merely timed out hands that wipe to a narrower index. Rows found by any source still
    # replace — those ARE this run's findings, and serving the previous set beside them
    # would present another keyword set's channels as this one's.
    # A FloodWait never replaces either: the run stopped mid-wave, so these rows are a
    # fraction of what the keywords would have found, the coordinator skips qualification
    # for them and reports the run failed, and the account is now on cooldown. Handing
    # that partial set to the delete-then-insert traded a reviewed, qualified candidate
    # list for a dozen unqualified handles the operator could not even re-search for.
    swept = any(outcome.answered for outcome in outcomes if outcome.source == "telegram_search")
    replaced = (
        not native.flooded
        and any(outcome.answered for outcome in outcomes)
        and (bool(rows) or swept)
    )
    if replaced:
        await replace_discovery_candidates(campaign_id, rows)
    return DiscoverySearchStageResult(
        # The stored count, so a run that kept the previous set does not report rows the
        # operator cannot see.
        found=len(rows) if replaced else 0,
        error=error,
        replaced=replaced,
        flooded=native.flooded,
        report=report,
    )
