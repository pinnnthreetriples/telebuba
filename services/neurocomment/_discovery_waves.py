"""Discovery stage 1, the Telegram half — every wave of one run and their shared budget.

Split from ``_discovery_search`` (file-size cap), which keeps the pure half: merge,
dedup, cap and the persist decision. The boundary is the network — nothing here reads
or writes the database, and nothing there talks to Telegram.

The run is a sequence of waves: the keyword sweep, the operator's own seed, the global
post pages, then Telegram's recommendations around the sweep's own best hits. They
multiply reads, so they share ONE budget (``discovery_max_reads_per_run`` per account
in the pool) spent in that order rather than each bounding itself — except that the
recommendation wave's reads are held back before the post pages run, because pure wave
order let the weakest source spend the last of the budget on itself. A wave the budget
stops reports itself truncated. Every read is made with the account the pool hands out
next; the pool drops an account that floods, that somebody else parked, or that answers
nothing ``discovery_max_consecutive_errors`` reads in a row, and the run ends the moment
the pool is empty — which is why the cooldown is re-read before EVERY read, not once per
wave: the keyword sweep alone can spend the whole budget.

Pacing note: every RPC is jittered exactly like the qualification pass. Even a modest
sweep is ~11 reads, and firing them as one burst is the freeze vector the whole
discovery design is built to avoid. That pacing is what sets the stage's duration
(~20s for a keyword-only sweep).
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from core.channel_tokens import dedup_key, normalize_channel
from core.config import settings
from schemas.neurocomment_discovery import CHANNEL_HANDLE_MAX_LENGTH
from services.neurocomment._discovery_categories import keywords_for
from services.neurocomment._discovery_providers import (
    SourceOutcome,
    search_global,
    search_native,
    search_similar,
)
from services.neurocomment._discovery_wave_support import (
    READ_BUDGET,
    Budget,
    Wave,
    pace,
    skipped,
    stopped,
    unreached,
)
from services.neurocomment._signals import signal_discovery_progress

if TYPE_CHECKING:
    from schemas.neurocomment_discovery import DiscoverySearchRequest
    from schemas.telegram_actions_discovery import DiscoveryKind, GlobalPostsCursor
    from services.neurocomment._discovery_pool import AccountPool

# Pages of ``messages.searchGlobal`` per keyword, the reads the post wave may spend in
# total, and channels of the keyword sweep that each get their own recommendations read.
# Module literals rather than settings: the run's ONE tunable ceiling is
# ``discovery_max_reads_per_run``, and per-wave knobs whose only real power was switching
# a source off (leaving the board a source short) bought nothing the budget does not
# already bound.
#
# Pages per keyword is the wave total split evenly, never below one: at two pages each a
# full keyword list wanted 20 reads, which is more than the sweep, the operator's seed and
# the recommendation wave put together — an enormous share for the weakest source. Its
# reach is narrower than "global post search" sounds, so it is the wave that yields.
_GLOBAL_MAX_PAGES = 2
_GLOBAL_MAX_READS = 10
_SIMILAR_FROM_TOP = 5


def sweep_keywords(request: DiscoverySearchRequest) -> list[str]:
    """The operator's keywords plus the category's bundle, deduped case-insensitively."""
    words: dict[str, str] = {}
    for word in [*request.keywords, *keywords_for(request.category)]:
        words.setdefault(word.casefold(), word)
    return list(words.values())


async def _report(pool: AccountPool, account_id: str, outcome: SourceOutcome) -> bool:
    """Hand one read's outcome to the pool; ``True`` means no account is left to read with."""
    return await pool.report(
        account_id,
        flood_seconds=outcome.flood_seconds,
        failed=not outcome.answered,
    )


async def _keyword_pass(
    pool: AccountPool,
    keywords: list[str],
    budget: Budget,
    kind: DiscoveryKind,
) -> Wave:
    """One paced search per keyword — the cheapest wave, so it is served first."""
    outcomes: list[SourceOutcome] = []
    for index, keyword in enumerate(keywords):
        if not budget.take():
            outcomes.append(skipped("telegram_search", READ_BUDGET, truncated=True))
            break
        if index:
            await pace()
        # AFTER the pace sleep: it is one to two seconds long, and a limit landing inside
        # it would otherwise still buy one read.
        account_id = pool.acquire()
        if account_id is None:
            return stopped(outcomes, pool)
        native = await search_native(account_id, keyword, kind)
        outcomes.append(native)
        # A full sweep is minutes of paced reads; without a nudge per keyword the
        # operator watches a frozen modal and clicks the button again.
        signal_discovery_progress()
        if await _report(pool, account_id, native):
            return stopped(outcomes, pool)
    return Wave(outcomes)


async def _global_pass(
    pool: AccountPool,
    keywords: list[str],
    budget: Budget,
    kind: DiscoveryKind,
) -> Wave:
    """Page the post index per keyword: channels whose posts match, not their titles.

    Bounded four ways, because the search never says "done": the pages this wave may
    spend per keyword, the run's read budget minus whatever it holds back for the
    recommendation wave, and a page that added no channel this keyword had not already
    produced. ``next_cursor`` is absent only when a page held no message at all, and
    ``limit`` counts messages rather than channels, so a short page is no end-of-results
    signal either.
    """
    outcomes: list[SourceOutcome] = []
    pages = min(_GLOBAL_MAX_PAGES, max(1, _GLOBAL_MAX_READS // len(keywords)))
    for keyword in keywords:
        seen: set[str] = set()
        cursor: GlobalPostsCursor | None = None
        for _page in range(pages):
            if not budget.take():
                outcomes.append(skipped("telegram_posts", READ_BUDGET, truncated=True))
                return Wave(outcomes)
            await pace()
            account_id = pool.acquire()
            if account_id is None:
                return stopped(outcomes, pool)
            page = await search_global(account_id, keyword, cursor, kind)
            outcomes.append(page.outcome)
            signal_discovery_progress()
            if await _report(pool, account_id, page.outcome):
                return stopped(outcomes, pool)
            fresh = {dedup_key(hit.ref) for hit in page.outcome.candidates} - seen
            if page.cursor is None or not fresh:
                break
            seen |= fresh
            cursor = page.cursor
    return Wave(outcomes)


def _seed_handle(request: DiscoverySearchRequest) -> str | None:
    """The operator's seed as a canonical handle, or ``None`` when it is not usable.

    Read by the seed pass and, so it can be excluded, by the recommendation wave.
    """
    if request.seed_channel is None:
        return None
    return normalize_channel(request.seed_channel, max_length=CHANNEL_HANDLE_MAX_LENGTH)


async def _seed_pass(pool: AccountPool, request: DiscoverySearchRequest, budget: Budget) -> Wave:
    """The operator's optional seed channel, unchanged, still its own report row."""
    seed = _seed_handle(request)
    if seed is None:
        # A seed the operator typed but which is not a usable handle spent a pace sleep
        # and a peer resolution for nothing, and said so nowhere. Keyed off the seed, not
        # off the flood: reporting "seed_unusable" for a flood sent the operator to edit a
        # seed that was perfectly fine.
        unusable = "seed_unusable" if request.seed_channel is not None else None
        return Wave([skipped("telegram_similar", unusable)])
    if not budget.take():
        return Wave([skipped("telegram_similar", READ_BUDGET, truncated=True)])
    await pace()
    account_id = pool.acquire()
    if account_id is None:
        # No outcome row: ``unreached`` names every source the run never got to, and a
        # reason here would compete with the run's own stop reason for the board's line.
        return stopped([], pool)
    similar = await search_similar(account_id, seed)
    if await _report(pool, account_id, similar):
        return stopped([similar], pool)
    return Wave([similar])


def _wave_seeds(outcomes: list[SourceOutcome], limit: int, spent: str | None = None) -> list[str]:
    """The keyword sweep's highest-value hits, as recommendation seeds.

    Value is the subscriber count where Telegram returned one (a large channel's
    recommendation neighbourhood is the richest), otherwise the relevance order the search
    returned. Ranked before the member filter: that filter says which channels the operator
    would COMMENT on, not which make good graph seeds.

    ``spent`` is the operator's own seed, which the seed pass has already asked
    ``getChannelRecommendations`` about in this same run: without it a sweep that also
    found that channel paid a second budgeted read for a reply Telegram gave us seconds
    ago, on the same peer.
    """
    hits = [hit for outcome in outcomes for hit in outcome.candidates]
    asked = None if spent is None else dedup_key(spent)
    seeds: dict[str, str] = {}
    for hit in sorted(hits, key=lambda hit: (hit.subscribers is None, -(hit.subscribers or 0))):
        if len(seeds) >= limit:
            break
        if hit.username is None:
            continue
        handle = normalize_channel(hit.username, max_length=CHANNEL_HANDLE_MAX_LENGTH)
        if handle is None:
            continue
        key = dedup_key(handle)
        if key == asked:
            continue
        seeds.setdefault(key, handle)
    return list(seeds.values())


async def _similar_wave(pool: AccountPool, seeds: list[str], budget: Budget) -> Wave:
    """Telegram's recommendations around each seed the sweep produced.

    Reported as ``telegram_recommended``, separate from the operator's seed pass: they
    answer different questions ("did MY seed help" vs "did the graph widen the sweep"),
    and one shared row would let whichever ran mask the other's reason.
    """
    outcomes: list[SourceOutcome] = []
    for seed in seeds:
        if not budget.take():
            outcomes.append(skipped("telegram_recommended", READ_BUDGET, truncated=True))
            return Wave(outcomes)
        await pace()
        account_id = pool.acquire()
        if account_id is None:
            return stopped(outcomes, pool)
        similar = await search_similar(account_id, seed, "telegram_recommended")
        outcomes.append(similar)
        signal_discovery_progress()
        if await _report(pool, account_id, similar):
            return stopped(outcomes, pool)
    return Wave(outcomes)


async def native_pass(pool: AccountPool, request: DiscoverySearchRequest) -> Wave:
    """Every Telegram wave of one run, under one shared read budget."""
    # Per account, not per run: the ceiling bounds what ONE session emits, and the pool
    # spreads the reads over all of them.
    budget = Budget(settings.neurocomment.discovery_max_reads_per_run * pool.size)
    keywords = sweep_keywords(request)
    sweep = await _keyword_pass(pool, keywords, budget, request.kind)
    outcomes = list(sweep.outcomes)
    last = sweep

    # The operator's seed is read BEFORE the automatic post pages, though the post wave is
    # the broader source: the seed is exactly ONE read, while the post pages want one per
    # page per keyword and drained the whole budget first — so from six keywords up, the
    # seed the operator explicitly typed never got its turn and BOTH recommendation
    # sources reported themselves out of budget. An explicit input does not lose its
    # single read to an automatic wave; the post wave is what absorbs the squeeze.
    if not last.stopped:
        seed = await _seed_pass(pool, request, budget)
        outcomes.extend(seed.outcomes)
        last = seed
    # Seeded from the keyword sweep only: those hits are Telegram's answer to what the
    # operator actually asked for, so they are the seeds worth a read apiece. Chosen here,
    # ahead of the post pages, because their exact count is what the budget holds back:
    # reserving the wave's literal ceiling instead would strand reads a short sweep is
    # never going to spend.
    seeds = _wave_seeds(sweep.outcomes, _SIMILAR_FROM_TOP, _seed_handle(request))
    if not last.stopped:
        budget.hold(len(seeds))
        posts = await _global_pass(pool, keywords, budget, request.kind)
        outcomes.extend(posts.outcomes)
        last = posts
    if not last.stopped:
        budget.hold(0)
        wave = await _similar_wave(pool, seeds, budget)
        outcomes.extend(wave.outcomes)
        last = wave
    return Wave(outcomes + unreached(outcomes), last.flooded, cooled=last.cooled)
