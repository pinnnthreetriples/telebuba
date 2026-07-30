"""Discovery stage 1 — fan out to the enabled sources, merge, persist candidates.

Pacing note: the keyword RPCs are jittered exactly like the qualification pass.
Even a modest sweep is ~11 reads, and firing them as one burst is the freeze
vector the whole discovery design is built to avoid. That pacing is what sets the
stage's duration (~20s for a full sweep); the catalogue queries run alongside it
and concurrently with each other, because HTTP to a third party costs no Telegram
flood budget and serially they were minutes, not seconds.
"""

from __future__ import annotations

import asyncio
from typing import TYPE_CHECKING, NamedTuple

from core.channel_tokens import dedup_key, normalize_channel
from core.config import settings
from core.db import load_warming_settings
from core.repositories.neurocomment import replace_discovery_candidates
from schemas.neurocomment_discovery import (
    CHANNEL_HANDLE_MAX_LENGTH,
    DiscoveryCandidateOrigin,
    DiscoveryCandidateRow,
    DiscoveryRunReport,
    DiscoverySearchStageResult,
    DiscoverySourceReport,
)
from services.neurocomment import _seams
from services.neurocomment._discovery_providers import (
    SourceOutcome,
    record_flood,
    search_native,
    search_similar,
    search_telemetr,
)
from services.neurocomment._signals import signal_discovery_progress

if TYPE_CHECKING:
    from schemas.neurocomment_discovery import (
        DiscoverySearchRequest,
        DiscoverySource,
        DiscoverySourceState,
    )
    from services.neurocomment._discovery_providers import RawCandidate

# Native hits win a cross-source tie: their handles come straight from Telegram in
# canonical case, which is what adopt writes into the campaign verbatim. This governs
# the DEDUP order only — see ``_merge`` for why it must not govern truncation.
# Annotated because the keys are also iterated as the report's source list, where a
# widened ``str`` would not satisfy ``DiscoverySourceReport``.
_SOURCE_PRIORITY: dict[DiscoverySource, int] = {
    "telegram_search": 0,
    "telegram_similar": 1,
    "telemetr": 2,
}


class _NativePass(NamedTuple):
    """The Telegram half of a run: its outcomes, and whether it hit a FloodWait."""

    outcomes: list[SourceOutcome]
    flooded: bool


async def _pace() -> None:
    neuro = settings.neurocomment
    await _seams.sleep(
        _seams.rng.uniform(
            neuro.discovery_qualify_delay_min_seconds,
            neuro.discovery_qualify_delay_max_seconds,
        ),
    )


def _within_member_bounds(subscribers: int | None, request: DiscoverySearchRequest) -> bool:
    """Re-apply the subscriber filter to hits whose count we happen to know.

    Telemetr filters server-side; native search usually returns no count at all, and
    an unknown count must not be silently dropped — qualification backfills it from
    the probe when it has to make one, and the operator can see it then.
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
    for source in _SOURCE_PRIORITY:
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
                reason=None if degraded is None else degraded.error,
                detail=None if degraded is None else degraded.detail,
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
    ranked.sort(key=lambda pair: _SOURCE_PRIORITY.get(pair[1].source, 99))
    entries = _normalized(ranked)

    # Pool the subscriber counts before deduping. A native hit carries no count and
    # outranks Telemetr, so otherwise it would shadow the very count that decides
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
        origin.country = origin.country or candidate.country
        origin.language = origin.language or candidate.language
        within = seen_per_group.get(group, 0)
        seen_per_group[group] = within + 1
        rank[key] = min(rank.get(key, within), within)

    # Interleave: each outcome's Nth hit, THEN the priority tiebreak. Priority governs the
    # dedup above (canonical spelling), and letting it govern truncation too put telemetr
    # permanently at the tail — a native sweep that filled the cap dropped every catalogue
    # row, so country and language influenced nothing at all. Per OUTCOME, not per source,
    # so the cap is shared across keywords as well as sources.
    selected = sorted(
        accepted,
        key=lambda key: (rank[key], _SOURCE_PRIORITY.get(accepted[key].source, 99)),
    )[: settings.neurocomment.discovery_max_candidates]
    kept_origins = {accepted[key].channel: origins[key] for key in selected}

    # First error wins: the board shows one short reason, not a concatenation. The
    # per-source report carries the rest.
    error = next((outcome.error for outcome in outcomes if outcome.error), None)
    report = DiscoveryRunReport(
        sources=_source_reports(outcomes, kept_origins),
        origins=kept_origins,
    )
    return [accepted[key] for key in selected], error, report


async def _native_pass(account_id: str, request: DiscoverySearchRequest) -> _NativePass:
    """The paced Telegram reads: one search per keyword, then the optional seed pass."""
    if request.catalogue_only:
        # Reported, not merely absent: "Telegram search: not queried" is what tells the
        # operator why the table is shorter and entirely locale-verified.
        return _NativePass(
            [
                SourceOutcome(source="telegram_search", state="skipped"),
                SourceOutcome(source="telegram_similar", state="skipped"),
            ],
            flooded=False,
        )
    outcomes: list[SourceOutcome] = []
    flooded = False
    for index, keyword in enumerate(request.keywords):
        if index:
            await _pace()
        native = await search_native(account_id, keyword)
        outcomes.append(native)
        # A full sweep is minutes of paced reads; without a nudge per keyword the
        # operator watches a frozen modal and clicks the button again.
        signal_discovery_progress()
        if await record_flood(account_id, native.error):
            # Every remaining read would land inside the live window, and Telegram
            # escalates the wait on repeat violations. Same rule the qualification
            # loop follows; recording it also keeps the retry off this account.
            flooded = True
            break

    seed = (
        None
        if request.seed_channel is None
        else normalize_channel(request.seed_channel, max_length=CHANNEL_HANDLE_MAX_LENGTH)
    )
    if flooded or seed is None:
        outcomes.append(
            SourceOutcome(
                source="telegram_similar",
                state="skipped",
                # A seed the operator typed but which is not a usable handle spent a pace
                # sleep and a peer resolution for nothing, and said so nowhere. Keyed off
                # the seed, not off the flood: reporting "seed_unusable" for a flood sent
                # the operator to edit a seed that was perfectly fine.
                error=(
                    "seed_unusable" if request.seed_channel is not None and seed is None else None
                ),
            ),
        )
        return _NativePass(outcomes, flooded)

    await _pace()
    similar = await search_similar(account_id, seed)
    outcomes.append(similar)
    return _NativePass(outcomes, await record_flood(account_id, similar.error))


async def _catalogue_pass(request: DiscoverySearchRequest) -> list[SourceOutcome]:
    """Every keyword's catalogue query, all at once.

    Concurrent and unpaced: these cost no Telegram flood budget, and awaited serially
    inside the keyword loop the worst case was ~7 minutes (20s timeout x 2 attempts x 10
    keywords) for a stage that advertises ~20s. The key is read once for the whole run
    because every read decrypts a secret.
    """
    if not request.use_telemetr:
        return [SourceOutcome(source="telemetr", state="skipped")]
    secret = await load_warming_settings()
    return list(
        await asyncio.gather(
            *(
                search_telemetr(keyword, request, secret.telemetr_api_key)
                for keyword in request.keywords
            ),
        ),
    )


def _filters_unapplied(request: DiscoverySearchRequest, outcomes: list[SourceOutcome]) -> bool:
    """Were country/language asked for while the only source that applies them stayed mute?

    The catalogue is the sole filter-aware source, so replacing a filtered set with
    unfiltered native rows silently downgrades the operator's data: the previous run's
    Turkish, already-qualified candidates would be deleted and replaced by whatever
    native search returned for the same keyword.
    """
    if request.country is None and request.language is None:
        return False
    return not any(outcome.answered for outcome in outcomes if outcome.source == "telemetr")


async def run_search(
    campaign_id: str,
    account_id: str,
    request: DiscoverySearchRequest,
) -> DiscoverySearchStageResult:
    """Collect candidates from every enabled source and persist the merged set.

    A source that fails is recorded, never raised: the other source's results still have
    value to the operator. The two halves run concurrently so that a Telegram FloodWait
    stops only the Telegram arm — the operator has already spent one of their daily
    search slots, and the catalogue is the only source their filters reach.

    A TaskGroup, not ``gather``: gather propagates the first exception WITHOUT cancelling
    its sibling, so an unexpected failure in either half would let the run be reported
    failed and its account released while the other half kept issuing paced Telegram reads
    on it — the operator's retry would then pass the busy check and start a second stream
    on one account, which is the exact mutual exclusion this whole module is built around.
    """
    async with asyncio.TaskGroup() as group:
        native_task = group.create_task(_native_pass(account_id, request))
        catalogue_task = group.create_task(_catalogue_pass(request))
    native = native_task.result()
    outcomes = [*native.outcomes, *catalogue_task.result()]

    rows, error, report = _merge(outcomes, request)
    # Nobody answered, so an empty merge is not a finding; or the filter-aware source did
    # not, so these rows are a worse answer than the ones already stored. The write is
    # delete-then-insert, so either would destroy the previous run's already-qualified
    # candidates over one transient failure.
    replaced = any(outcome.answered for outcome in outcomes) and not _filters_unapplied(
        request,
        outcomes,
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
