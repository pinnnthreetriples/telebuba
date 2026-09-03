"""Discovery stage 1 — merge what the sources returned and persist the candidates.

The Telegram half (every wave and the read budget they share) lives in
``_discovery_waves``; this module is the pure one, and the only one that writes.

A wave the run's read budget stopped reports itself truncated, which is NOT a run
error; a FloodWait ends every later wave AND stops the run replacing the stored
candidates with its partial findings.
"""

from __future__ import annotations

from collections import Counter
from typing import TYPE_CHECKING, NamedTuple

from core.channel_tokens import dedup_key, normalize_channel
from core.repositories.neurocomment import list_seen, replace_discovery_candidates
from schemas.neurocomment_discovery import (
    CHANNEL_HANDLE_MAX_LENGTH,
    DiscoveryCandidateOrigin,
    DiscoveryCandidateRow,
    DiscoveryRunReport,
    DiscoverySearchStageResult,
    DiscoverySourceReport,
)
from services.neurocomment._discovery_filters import admit_at_search
from services.neurocomment._discovery_providers import COOLING_REASON
from services.neurocomment._discovery_wave_support import SOURCE_PRIORITY
from services.neurocomment._discovery_waves import native_pass

if TYPE_CHECKING:
    from schemas.neurocomment_discovery import (
        DiscoverySearchRequest,
        DiscoverySource,
        DiscoverySourceState,
    )
    from services.neurocomment._discovery_pool import AccountPool
    from services.neurocomment._discovery_providers import RawCandidate, SourceOutcome


class _Merged(NamedTuple):
    """The merged candidate set, plus everything the run report is built from."""

    rows: list[DiscoveryCandidateRow]
    error: str | None
    origins: dict[str, DiscoveryCandidateOrigin]
    # Distinct usable channels each source returned — the honest denominator of the
    # board's "kept of hits", see ``DiscoverySourceReport.hits``.
    reach: dict[DiscoverySource, set[str]]
    # Distinct channels each operator filter refused, by filter name.
    filtered: dict[str, int]
    # Did the candidate cap drop a tail this merge had?
    capped: bool = False


def _within_member_bounds(subscribers: int | None, request: DiscoverySearchRequest) -> bool:
    """Apply the subscriber filter to hits whose count we happen to know.

    Native search usually returns no count at all, and an unknown count must not be
    silently dropped — qualification backfills it from the probe when it has to make
    one, and the operator can see it then. What the row must NOT do is arrive looking
    filtered: it is flagged ``uncounted`` so the board can say the bounds never applied
    to it, whatever number the probe later writes beside it.
    """
    if subscribers is None:
        return True
    if request.members_min is not None and subscribers < request.members_min:
        return False
    return not (request.members_max is not None and subscribers > request.members_max)


def _handle_of(candidate: RawCandidate) -> str | None:
    """The stored form of one hit — canonical handle, or ``id:<n>`` when it has none.

    ``None`` for an unusable hit: invite-only links have no public handle to search or
    comment under.
    """
    if candidate.username is None:
        return candidate.ref
    handle = normalize_channel(candidate.username, max_length=CHANNEL_HANDLE_MAX_LENGTH)
    if handle is None or handle.startswith("+"):
        return None
    return handle


def _normalized(
    ranked: list[tuple[int, RawCandidate]],
    request: DiscoverySearchRequest,
    seen: set[str],
) -> tuple[list[tuple[str, str, int, RawCandidate]], dict[str, int]]:
    """Ranked hits paired with their dedup key, stored handle and outcome, plus the drops.

    Unusable hits vanish; hits an operator filter refuses are counted per filter and per
    DISTINCT channel, so three sources returning the same group read as one drop.

    The outcome index rides along because the interleave below shares the cap between
    outcomes, not between sources: one counter per source made rank a position in the
    concatenation of every keyword's hits, so keyword 0 filled the cap and the rest of
    the sweep was paid for and thrown away.
    """
    entries: list[tuple[str, str, int, RawCandidate]] = []
    rejected: dict[str, str] = {}
    for group, candidate in ranked:
        handle = _handle_of(candidate)
        if handle is None:
            continue
        key = dedup_key(handle)
        reason = admit_at_search(access=candidate.access, ref=key, request=request, seen=seen)
        if reason is not None:
            rejected.setdefault(key, reason)
            continue
        entries.append((key, handle, group, candidate))
    return entries, dict(Counter(rejected.values()))


def _source_reports(
    outcomes: list[SourceOutcome],
    reach: dict[DiscoverySource, set[str]],
    origins: dict[str, DiscoveryCandidateOrigin],
) -> list[DiscoverySourceReport]:
    """One report per source considered, in priority order.

    ``origins`` covers the rows this run actually STORED, and is empty when it stored
    none: a flood leaves the previous run's candidates in the table, and crediting
    ``kept``/``exclusive`` there described rows that exist nowhere.
    """
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
                hits=len(reach.get(source, ())),
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
    seen: set[str],
) -> _Merged:
    """Normalize, filter, dedup, interleave and cap the union of every source's hits.

    ``seen`` is the already-shown set the caller read for ``hide_seen`` (empty when the
    operator did not ask), so this stays a pure function of its arguments.
    """
    ranked: list[tuple[int, RawCandidate]] = []
    for group, outcome in enumerate(outcomes):
        ranked.extend((group, candidate) for candidate in outcome.candidates)
    # Stable sort by source priority so the dedup below keeps the preferred spelling.
    ranked.sort(key=lambda pair: SOURCE_PRIORITY.get(pair[1].source, 99))
    entries, filtered = _normalized(ranked, request, seen)

    # Pool the subscriber counts before deduping: a hit that carries no count can
    # outrank one that does, and would otherwise shadow the very count that decides
    # whether the channel passes the member filter at all. Each source's own distinct
    # reach is pooled in the same pass — one keyword's hits are not another's find.
    counts: dict[str, int] = {}
    reach: dict[DiscoverySource, set[str]] = {}
    for key, _handle, _group, candidate in entries:
        reach.setdefault(candidate.source, set()).add(key)
        if candidate.subscribers is not None:
            counts.setdefault(key, candidate.subscribers)
    # Only worth flagging when the operator asked for a bound at all.
    bounded = request.members_min is not None or request.members_max is not None

    accepted: dict[str, DiscoveryCandidateRow] = {}
    origins: dict[str, DiscoveryCandidateOrigin] = {}
    # The priority of the source whose spelling won, kept beside the row: the row's own
    # ``source`` is a free string (it is written to a table older builds also wrote to).
    winner: dict[str, int] = {}
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
                kind=candidate.kind,
            )
            origins[key] = DiscoveryCandidateOrigin(uncounted=bounded and subscribers is None)
            winner[key] = SOURCE_PRIORITY.get(candidate.source, 99)
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
    ordered = sorted(accepted, key=lambda key: (rank[key], winner[key]))
    # The operator's own ceiling is the only one.
    cap = request.limit
    selected = ordered[:cap]
    kept_origins = {accepted[key].channel: origins[key] for key in selected}

    # First FAILURE wins: the board shows one short reason, not a concatenation. The
    # per-source report carries the rest. A skip is not one of them, whatever its reason:
    # the read budget runs out on every full keyword list, and a groups search skips both
    # recommendation waves by design — painting a complete answer as a degraded one made
    # the normal case look broken, and the skip's reason then masked a real failure that
    # came later. The source's own row still names it.
    error = next((out.error for out in outcomes if out.state == "failed"), None)
    return _Merged(
        rows=[accepted[key] for key in selected],
        error=error,
        origins=kept_origins,
        reach=reach,
        capped=len(ordered) > cap,
        filtered=filtered,
    )


def _replaces(
    outcomes: list[SourceOutcome], merged: _Merged, stop: str | None
) -> tuple[bool, bool]:
    """Whether this run's rows displace the stored set, and whether ``seen`` alone emptied it.

    The write is delete-then-insert, so an empty merge nobody answered for would destroy
    the previous run's already-qualified candidates over one transient failure. An empty
    merge also needs the KEYWORD SWEEP to have answered: the wider waves are consulted on
    every run, and letting one of them answer "nothing" for a sweep that merely timed out
    hands that wipe to a narrower index. Rows found by any source still replace — those
    ARE this run's findings, and serving the previous set beside them would present
    another keyword set's channels as this one's.

    A flood never replaces either: the run stopped mid-wave, so these rows are a fraction
    of what the keywords would have found, the coordinator skips qualification for them and
    reports the run failed, and the account is now on cooldown. Handing that partial set to
    the delete-then-insert traded a reviewed, qualified candidate list for a dozen
    unqualified handles the operator could not even re-search for. A limit found at a
    wave boundary is treated exactly like one this run caused — reported under its own
    reason, because "we hit a flood" and "the account was already serving one" send the
    operator to different places.

    An empty merge that ``hide_seen`` ALONE emptied does not replace: every hit was a
    channel the operator already looked at, and wiping the previous set to show them
    nothing threw away the only rows that were still an answer. The board says those rows
    are the previous search's (``stored=False``), as after a flood.
    """
    swept = any(outcome.answered for outcome in outcomes if outcome.source == "telegram_search")
    all_seen = not merged.rows and {name for name, n in merged.filtered.items() if n} == {"seen"}
    replaced = (
        stop not in {"flooded", "cooling"}
        and any(outcome.answered for outcome in outcomes)
        and (bool(merged.rows) or swept)
        and not all_seen
    )
    return replaced, all_seen


async def run_search(
    campaign_id: str,
    pool: AccountPool,
    request: DiscoverySearchRequest,
) -> DiscoverySearchStageResult:
    """Collect candidates from every enabled source and persist the merged set.

    A source that fails is recorded, never raised: the other source's results still have
    value to the operator.
    """
    native = await native_pass(pool, request)
    outcomes = native.outcomes

    seen: set[str] = set()
    if request.hide_seen:
        # One bulk read, answered as dedup keys, so the merge can stay pure.
        refs = {_handle_of(hit) for outcome in outcomes for hit in outcome.candidates}
        seen = await list_seen(ref for ref in refs if ref is not None)
    merged = _merge(outcomes, request, seen)
    replaced, all_seen = _replaces(outcomes, merged, native.stop)
    if replaced:
        await replace_discovery_candidates(campaign_id, merged.rows)
    # The whole report is built AFTER that decision. Per-source states and reach describe
    # what the sources did, which is true either way — but everything that describes the
    # STORED rows (per-row origins, ``kept``, ``exclusive``, the cap) belongs to a set the
    # operator can see. A run stopped by a flood stored nothing, and reporting rows it
    # "kept" beside the previous run's table credited channels that exist nowhere.
    origins = merged.origins if replaced else {}
    return DiscoverySearchStageResult(
        # The stored count, so a run that kept the previous set does not report rows the
        # operator cannot see.
        found=len(merged.rows) if replaced else 0,
        # The stop wins over a source's own reason: a keyword that failed while the
        # account was being parked by something else explains nothing the operator can
        # act on, and the cooldown does.
        error=COOLING_REASON if native.stop == "cooling" else merged.error,
        replaced=replaced,
        all_seen=all_seen,
        flooded=native.stop in {"flooded", "cooling"},
        report=DiscoveryRunReport(
            sources=_source_reports(outcomes, merged.reach, origins),
            origins=origins,
            stored=replaced,
            capped=replaced and merged.capped,
            filtered=merged.filtered,
        ),
    )
