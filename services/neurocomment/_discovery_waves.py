"""Discovery stage 1, the Telegram half — every wave of one run and their shared budget.

Split from ``_discovery_search`` (file-size cap), which keeps the pure half: merge,
dedup, cap and the persist decision. The boundary is the network — nothing here reads
or writes the database, and nothing there talks to Telegram.

The run is a sequence of waves: the keyword sweep, the operator's own seed, the global
post pages, then Telegram's recommendations around the sweep's own best hits. They
multiply reads, so they share ONE budget (``discovery_max_reads_per_run``) spent in
that order rather than each bounding itself. A wave the budget stops reports itself
truncated; a FloodWait in any wave ends every later one.

Pacing note: every RPC is jittered exactly like the qualification pass. Even a modest
sweep is ~11 reads, and firing them as one burst is the freeze vector the whole
discovery design is built to avoid. That pacing is what sets the stage's duration
(~20s for a keyword-only sweep).
"""

from __future__ import annotations

from typing import TYPE_CHECKING, NamedTuple

from core.channel_tokens import dedup_key, normalize_channel
from core.config import settings
from schemas.neurocomment_discovery import CHANNEL_HANDLE_MAX_LENGTH
from services.neurocomment import _seams
from services.neurocomment._discovery_providers import (
    SourceOutcome,
    record_flood,
    search_global,
    search_native,
    search_similar,
)
from services.neurocomment._signals import signal_discovery_progress

if TYPE_CHECKING:
    from schemas.neurocomment_discovery import DiscoverySearchRequest, DiscoverySource
    from schemas.telegram_actions_discovery import GlobalPostsCursor

# Short locale-neutral reason for a wave the run's read budget stopped. Deliberately not
# a run-level error — see ``_discovery_search._merge``.
READ_BUDGET = "read_budget"

# Pages of ``messages.searchGlobal`` per keyword, and channels of the keyword sweep that
# each get their own recommendations read. Module literals rather than settings: the run's
# ONE tunable ceiling is ``discovery_max_reads_per_run``, and per-wave knobs whose only
# real power was switching a source off (leaving the board a source short) bought nothing
# the budget does not already bound.
_GLOBAL_MAX_PAGES = 2
_SIMILAR_FROM_TOP = 5

# Native hits win a cross-source tie: their handles come straight from Telegram in
# canonical case, which is what adopt writes into the campaign verbatim. This governs
# the DEDUP order only — see ``_discovery_search._merge`` for why it must not govern
# truncation as well.
# Annotated because the keys are also iterated as the report's source list, where a
# widened ``str`` would not satisfy ``DiscoverySourceReport``.
# Also the board's row order — which the run's wave order no longer follows, since the
# operator's seed is read ahead of the wider post wave.
SOURCE_PRIORITY: dict[DiscoverySource, int] = {
    "telegram_search": 0,
    "telegram_posts": 1,
    "telegram_similar": 2,
    "telegram_recommended": 3,
}


class Wave(NamedTuple):
    """One wave's outcomes, and whether it ended on a FloodWait."""

    outcomes: list[SourceOutcome]
    flooded: bool = False


class _Budget:
    """Telegram reads the run has left, shared by every wave.

    One counter, not a cap per wave: what has to stay bounded is the traffic this single
    account emits in one run, and spending it in wave order is what puts the cheap
    keyword sweep first in line for it.
    """

    def __init__(self, total: int) -> None:
        self.left = total

    def take(self) -> bool:
        """Claim one read. ``False`` means the run is out and the wave must stop."""
        if self.left <= 0:
            return False
        self.left -= 1
        return True


def _skipped(
    source: DiscoverySource,
    reason: str | None = None,
    *,
    truncated: bool = False,
) -> SourceOutcome:
    """A source that was not asked (again) — never a silent absence from the report."""
    return SourceOutcome(source=source, state="skipped", error=reason, truncated=truncated)


async def _pace() -> None:
    neuro = settings.neurocomment
    await _seams.sleep(
        _seams.rng.uniform(
            neuro.discovery_qualify_delay_min_seconds,
            neuro.discovery_qualify_delay_max_seconds,
        ),
    )


async def _keyword_pass(account_id: str, keywords: list[str], budget: _Budget) -> Wave:
    """One paced search per keyword — the cheapest wave, so it is served first."""
    outcomes: list[SourceOutcome] = []
    for index, keyword in enumerate(keywords):
        if not budget.take():
            outcomes.append(_skipped("telegram_search", READ_BUDGET, truncated=True))
            break
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
            return Wave(outcomes, flooded=True)
    return Wave(outcomes)


async def _global_pass(account_id: str, keywords: list[str], budget: _Budget) -> Wave:
    """Page the post index per keyword: channels whose posts match, not their titles.

    Bounded three ways, because the search never says "done": the page literal, the run's
    read budget, and a page that added no channel this keyword had not already produced.
    ``next_cursor`` is absent only when a page held no message at all, and ``limit`` counts
    messages rather than channels, so a short page is no end-of-results signal either.
    """
    outcomes: list[SourceOutcome] = []
    for keyword in keywords:
        seen: set[str] = set()
        cursor: GlobalPostsCursor | None = None
        for _page in range(_GLOBAL_MAX_PAGES):
            if not budget.take():
                outcomes.append(_skipped("telegram_posts", READ_BUDGET, truncated=True))
                return Wave(outcomes)
            await _pace()
            page = await search_global(account_id, keyword, cursor)
            outcomes.append(page.outcome)
            signal_discovery_progress()
            if await record_flood(account_id, page.outcome.error):
                return Wave(outcomes, flooded=True)
            fresh = {dedup_key(hit.username) for hit in page.outcome.candidates} - seen
            if page.cursor is None or not fresh:
                break
            seen |= fresh
            cursor = page.cursor
    return Wave(outcomes)


async def _seed_pass(account_id: str, request: DiscoverySearchRequest, budget: _Budget) -> Wave:
    """The operator's optional seed channel, unchanged, still its own report row."""
    seed = (
        None
        if request.seed_channel is None
        else normalize_channel(request.seed_channel, max_length=CHANNEL_HANDLE_MAX_LENGTH)
    )
    if seed is None:
        # A seed the operator typed but which is not a usable handle spent a pace sleep
        # and a peer resolution for nothing, and said so nowhere. Keyed off the seed, not
        # off the flood: reporting "seed_unusable" for a flood sent the operator to edit a
        # seed that was perfectly fine.
        unusable = "seed_unusable" if request.seed_channel is not None else None
        return Wave([_skipped("telegram_similar", unusable)])
    if not budget.take():
        return Wave([_skipped("telegram_similar", READ_BUDGET, truncated=True)])
    await _pace()
    similar = await search_similar(account_id, seed)
    return Wave([similar], await record_flood(account_id, similar.error))


def _wave_seeds(outcomes: list[SourceOutcome], limit: int) -> list[str]:
    """The keyword sweep's highest-value hits, as recommendation seeds.

    Value is the subscriber count where Telegram returned one (a large channel's
    recommendation neighbourhood is the richest), otherwise the relevance order the search
    returned. Ranked before the member filter: that filter says which channels the operator
    would COMMENT on, not which make good graph seeds.
    """
    hits = [hit for outcome in outcomes for hit in outcome.candidates]
    seeds: dict[str, str] = {}
    for hit in sorted(hits, key=lambda hit: (hit.subscribers is None, -(hit.subscribers or 0))):
        if len(seeds) >= limit:
            break
        handle = normalize_channel(hit.username, max_length=CHANNEL_HANDLE_MAX_LENGTH)
        if handle is None or handle.startswith("+"):
            continue
        seeds.setdefault(dedup_key(handle), handle)
    return list(seeds.values())


async def _similar_wave(account_id: str, seeds: list[str], budget: _Budget) -> Wave:
    """Telegram's recommendations around each seed the sweep produced.

    Reported as ``telegram_recommended``, separate from the operator's seed pass: they
    answer different questions ("did MY seed help" vs "did the graph widen the sweep"),
    and one shared row would let whichever ran mask the other's reason.
    """
    outcomes: list[SourceOutcome] = []
    for seed in seeds:
        if not budget.take():
            outcomes.append(_skipped("telegram_recommended", READ_BUDGET, truncated=True))
            return Wave(outcomes)
        await _pace()
        similar = await search_similar(account_id, seed, "telegram_recommended")
        outcomes.append(similar)
        signal_discovery_progress()
        if await record_flood(account_id, similar.error):
            return Wave(outcomes, flooded=True)
    return Wave(outcomes)


def _unreached(outcomes: list[SourceOutcome]) -> list[SourceOutcome]:
    """Name every source this run never got to, so none is silently absent from the strip.

    A flood, or a sweep with no hit to seed the recommendation wave from, used to drop a
    whole row off the board — leaving the operator to guess whether that source had
    answered with nothing or had never been asked. No reason code: a FloodWait already
    rides the run's error and a second one would only compete with it.
    """
    reached = {outcome.source for outcome in outcomes}
    return [_skipped(source) for source in SOURCE_PRIORITY if source not in reached]


async def native_pass(account_id: str, request: DiscoverySearchRequest) -> Wave:
    """Every Telegram wave of one run, under one shared read budget."""
    budget = _Budget(settings.neurocomment.discovery_max_reads_per_run)
    keywords = await _keyword_pass(account_id, request.keywords, budget)
    outcomes = list(keywords.outcomes)
    flooded = keywords.flooded

    # The operator's seed is read BEFORE the automatic post pages, though the post wave is
    # the broader source: the seed is exactly ONE read, while the post pages want one per
    # page per keyword and drained the whole budget first — so from six keywords up, the
    # seed the operator explicitly typed never got its turn and BOTH recommendation
    # sources reported themselves out of budget. An explicit input does not lose its
    # single read to an automatic wave; the post wave is what absorbs the squeeze.
    if not flooded:
        seed = await _seed_pass(account_id, request, budget)
        outcomes.extend(seed.outcomes)
        flooded = seed.flooded
    if not flooded:
        posts = await _global_pass(account_id, request.keywords, budget)
        outcomes.extend(posts.outcomes)
        flooded = posts.flooded
    if not flooded:
        # Seeded from the keyword sweep only: those hits are Telegram's answer to what the
        # operator actually asked for, so they are the seeds worth a read apiece.
        seeds = _wave_seeds(keywords.outcomes, _SIMILAR_FROM_TOP)
        wave = await _similar_wave(account_id, seeds, budget)
        outcomes.extend(wave.outcomes)
        flooded = wave.flooded
    return Wave(outcomes + _unreached(outcomes), flooded)
