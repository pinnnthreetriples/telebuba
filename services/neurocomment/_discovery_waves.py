"""Discovery stage 1, the Telegram half — every wave of one run and their shared budget.

Split from ``_discovery_search`` (file-size cap), which keeps the pure half: merge,
dedup, cap and the persist decision. The boundary is the network — nothing here reads
or writes the database, and nothing there talks to Telegram.

A run is a set of JOBS handed to ``services.neurocomment._discovery_streams.Streams``,
which runs one paced stream per pool account concurrently: the keyword sweep, the
operator's own seed, the global post pages, then Telegram's recommendations around the
sweep's own best hits. They multiply reads, so they share ONE budget
(``discovery_max_reads_per_run`` per account in the pool), spent in wave-order priority
(``Job.order``) rather than each bounding itself — except that the recommendation wave's
reads are held back before the post pages run, because pure wave order let the weakest
source spend the last of the budget on itself. The hold is fenced off from the START
(``budget.held = _SIMILAR_FROM_TOP``), because several streams can be spending the post
wave's budget before the keyword sweep — the source the hold is sized from — has even
finished; the LAST keyword job to finish narrows it to the exact count once the real
seeds are known. A wave the budget stops reports itself truncated. Every read is made
with whichever account the scheduler hands the job to next — Premium first for the reads
Telegram answers better on Premium; the pool drops an account that floods, that somebody
else parked, or that answers nothing ``discovery_max_consecutive_errors`` reads in a row,
and the run ends the moment the pool is empty.

Pacing note: every RPC is jittered exactly like the qualification pass, but the gap is
now per STREAM — two reads of the SAME account, never a gap between two different
accounts' reads. Firing every account's reads as one burst is the freeze vector the
whole discovery design is built to avoid; running several accounts at once is what lets
the stage finish in the time of its SLOWEST stream instead of the sum of all of them.
"""

from __future__ import annotations

from dataclasses import dataclass
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
from services.neurocomment._discovery_streams import Job, JobResult, Streams
from services.neurocomment._discovery_wave_support import (
    KIND_UNSUPPORTED,
    Budget,
    Wave,
    skipped,
    unreached,
)

if TYPE_CHECKING:
    from schemas.neurocomment_discovery import DiscoverySearchRequest
    from schemas.telegram_actions_discovery import DiscoveryKind, GlobalPostsCursor
    from services.neurocomment._discovery_pool import AccountPool
    from services.neurocomment._discovery_state import WorkTracker

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
# reach is narrower than "global post search" sounds, so it is the wave that yields. The
# total is enforced as such, too: a category bundle on a full keyword list is 18 words,
# and one page each was still 18 reads.
_GLOBAL_MAX_PAGES = 2
_GLOBAL_MAX_READS = 10
_SIMILAR_FROM_TOP = 5


def sweep_keywords(request: DiscoverySearchRequest) -> list[str]:
    """The operator's keywords plus the category's bundle, deduped case-insensitively."""
    words: dict[str, str] = {}
    for word in [*request.keywords, *keywords_for(request.category)]:
        words.setdefault(word.casefold(), word)
    return list(words.values())


def _seed_handle(request: DiscoverySearchRequest) -> str | None:
    """The operator's seed as a canonical handle, or ``None`` when it is not usable.

    Read by the seed job and, so it can be excluded, by the recommendation seeds.
    """
    if request.seed_channel is None:
        return None
    return normalize_channel(request.seed_channel, max_length=CHANNEL_HANDLE_MAX_LENGTH)


def _wave_seeds(outcomes: list[SourceOutcome], limit: int, spent: str | None = None) -> list[str]:
    """The keyword sweep's highest-value CHANNEL hits, as recommendation seeds.

    Value is the subscriber count where Telegram returned one (a large channel's
    recommendation neighbourhood is the richest), otherwise the relevance order the search
    returned. Ranked before the member filter: that filter says which channels the operator
    would COMMENT on, not which make good graph seeds. Groups are no seeds at all —
    recommendations are computed for channels.

    ``spent`` is the operator's own seed, which the seed job has already asked
    ``getChannelRecommendations`` about in this same run: without it a sweep that also
    found that channel paid a second budgeted read for a reply Telegram gave us seconds
    ago, on the same peer.
    """
    hits = [hit for outcome in outcomes for hit in outcome.candidates if hit.kind == "channel"]
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


def _retry_result(
    outcome: SourceOutcome,
    attempt: int,
    *,
    followups: tuple[Job, ...] = (),
) -> JobResult:
    """The common shape every wave job returns: retry once on a flood or a dead client.

    ``attempt`` is 0 on the first try, 1 on the one retry the scheduler grants — see
    ``services.neurocomment._discovery_streams.Job.attempt``. A retry-eligible outcome
    is not final until that retry has actually run, so it is never the caller's to
    record until ``attempt`` says this WAS the last try.
    """
    retry = outcome.flood_seconds is not None or outcome.unreachable
    return JobResult(
        flood_seconds=outcome.flood_seconds,
        failed=not outcome.answered,
        followups=followups,
        error=outcome.error,
        retry=retry and attempt == 0,
        unreachable=outcome.unreachable,
    )


def _final(outcome: SourceOutcome, attempt: int) -> bool:
    """Is this outcome the one to actually record — not a flood/dead try awaiting its retry?"""
    return not (outcome.flood_seconds is not None or outcome.unreachable) or attempt == 1


@dataclass(slots=True)
class _WaveContext:
    """Shared state every wave job writes its outcome into or spends from.

    Bundled so job factories stay under the arg-count limit rather than threading four
    unrelated shared references through each one individually.

    ``pending`` holds a retry-eligible read's outcome, keyed by a token private to that
    one job, until its retry actually runs — cleared either way once it does. If the
    pool empties before that retry gets a turn (the account it would have gone to was
    the one just dropped), the outcome would otherwise vanish with no trace: nothing
    ever ran it a second time, and the first try was deliberately never recorded.
    ``native_pass`` promotes whatever is left in here once the run truly stops.
    """

    outcomes: list[SourceOutcome]
    budget: Budget
    work: WorkTracker
    pending: dict[object, SourceOutcome]


def _record(ctx: _WaveContext, token: object, outcome: SourceOutcome, attempt: int) -> None:
    """File this outcome as final, or hold it pending the one retry it earned."""
    if _final(outcome, attempt):
        ctx.pending.pop(token, None)
        ctx.outcomes.append(outcome)
    else:
        ctx.pending[token] = outcome


def _seed_job(seed: str, kind: DiscoveryKind, ctx: _WaveContext) -> Job:
    """The operator's optional seed channel, read once, still its own report row."""
    token = object()

    async def run(account_id: str, attempt: int) -> JobResult:
        outcome = await search_similar(account_id, seed, kind=kind)
        _record(ctx, token, outcome, attempt)
        return _retry_result(outcome, attempt)

    return Job(source="telegram_similar", run=run, order=1, premium=True)


def _recommendation_job(seed: str, kind: DiscoveryKind, ctx: _WaveContext) -> Job:
    """Telegram's recommendations around one seed the keyword sweep produced.

    Reported as ``telegram_recommended``, separate from the operator's seed job: they
    answer different questions ("did MY seed help" vs "did the graph widen the sweep"),
    and one shared row would let whichever ran mask the other's reason.
    """
    token = object()

    async def run(account_id: str, attempt: int) -> JobResult:
        outcome = await search_similar(account_id, seed, "telegram_recommended", kind)
        _record(ctx, token, outcome, attempt)
        return _retry_result(outcome, attempt)

    return Job(source="telegram_recommended", run=run, order=3, premium=True, held=True)


@dataclass(slots=True)
class _SweepState:
    """The keyword sweep's own bookkeeping: who finishes last, and their hits so far."""

    remaining: list[int]
    outcomes: list[SourceOutcome]
    seed_handle: str | None


def _keyword_job(keyword: str, kind: DiscoveryKind, sweep: _SweepState, ctx: _WaveContext) -> Job:
    """One paced search per keyword — the cheapest wave, so it runs at the lowest order.

    The LAST of these to finish (``sweep.remaining`` hits zero) seeds the recommendation
    wave: only then has the sweep produced every hit it is going to, which is what the
    seeds are ranked from. Never for a groups search — recommendations only ever return
    channels, and that case is refused up front by the caller.
    """
    token = object()

    async def run(account_id: str, attempt: int) -> JobResult:
        native = await search_native(account_id, keyword, kind)
        followups: tuple[Job, ...] = ()
        if _final(native, attempt):
            sweep.outcomes.append(native)
            sweep.remaining[0] -= 1
            if sweep.remaining[0] == 0 and kind != "groups":
                # Narrows the opening guess (``_SIMILAR_FROM_TOP``) to the real count,
                # which frees whatever it over-reserved back to the post wave.
                seeds = _wave_seeds(sweep.outcomes, _SIMILAR_FROM_TOP, sweep.seed_handle)
                ctx.budget.held = len(seeds)
                ctx.work.extra = 0
                followups = tuple(_recommendation_job(seed, kind, ctx) for seed in seeds)
        _record(ctx, token, native, attempt)
        return _retry_result(native, attempt, followups=followups)

    return Job(source="telegram_search", run=run, order=0)


@dataclass(slots=True)
class _PostReservation:
    """How many more post-page reads the wave may still spend, shared across keywords.

    Reserved on creation of EVERY page job (the first page of each keyword, and every
    followup page), not on the read itself: several streams can be paging different
    keywords at once, and the total must still land on ``_GLOBAL_MAX_READS`` exactly.
    """

    left: int = _GLOBAL_MAX_READS

    def take(self) -> bool:
        if self.left <= 0:
            return False
        self.left -= 1
        return True


@dataclass(slots=True)
class _PostPage:
    """Where one post-page job continues from."""

    cursor: GlobalPostsCursor | None
    seen: set[str]
    pages_left: int


def _post_job(
    keyword: str,
    kind: DiscoveryKind,
    page: _PostPage,
    reservation: _PostReservation,
    ctx: _WaveContext,
) -> Job:
    """One page of the global post index for one keyword: channels whose posts match.

    Bounded four ways, because the search never says "done": the pages a keyword may
    spend (``page.pages_left``), the wave's own total (``reservation``), the run's shared
    read budget (checked by the scheduler), and a page that added no channel this
    keyword had not already produced. ``next_cursor`` is absent only when a page held no
    message at all, and ``limit`` counts messages rather than channels, so a short page
    is no end-of-results signal either. Premium first: ``searchGlobal`` answers a
    non-premium account with FLOOD_PREMIUM_WAIT.
    """
    token = object()

    async def run(account_id: str, attempt: int) -> JobResult:
        fetched = await search_global(account_id, keyword, page.cursor, kind)
        followups: tuple[Job, ...] = ()
        # Post-page followups only from an answered page, as before: a flood or a dead
        # client carries no cursor, so a retry-pending try never queues one either way.
        if _final(fetched.outcome, attempt):
            fresh = {dedup_key(hit.ref) for hit in fetched.outcome.candidates} - page.seen
            if fetched.cursor is not None and fresh and page.pages_left > 0 and reservation.take():
                next_page = _PostPage(fetched.cursor, page.seen | fresh, page.pages_left - 1)
                followups = (_post_job(keyword, kind, next_page, reservation, ctx),)
        _record(ctx, token, fetched.outcome, attempt)
        return _retry_result(fetched.outcome, attempt, followups=followups)

    return Job(source="telegram_posts", run=run, order=2, premium=True)


async def native_pass(
    pool: AccountPool,
    request: DiscoverySearchRequest,
    work: WorkTracker,
) -> Wave:
    """Every Telegram wave of one run, as concurrent jobs under one shared read budget."""
    # Per account, not per run: the ceiling bounds what ONE session emits, and the pool
    # spreads the reads over all of them (and caps each one's share itself).
    budget = Budget(settings.neurocomment.discovery_max_reads_per_run * pool.size)
    kind = request.kind
    outcomes: list[SourceOutcome] = []
    ctx = _WaveContext(outcomes=outcomes, budget=budget, work=work, pending={})
    jobs: list[Job] = []

    if kind == "groups":
        # Recommendations only ever return channels: a groups-only search refuses both
        # recommendation sources up front rather than spending reads on a guaranteed
        # empty answer (and, on a dead seed, a failed-probe cascade).
        outcomes.append(skipped("telegram_similar", KIND_UNSUPPORTED))
        outcomes.append(skipped("telegram_recommended", KIND_UNSUPPORTED))
        seed_handle = None
    else:
        # Fenced off before a single read runs: streams reading the sweep and the post
        # wave can both be spending already by the time the real seed count is known.
        budget.held = _SIMILAR_FROM_TOP
        work.extra = _SIMILAR_FROM_TOP
        seed_handle = _seed_handle(request)
        if seed_handle is None:
            # A seed the operator typed but which is not a usable handle would otherwise
            # spend nothing and say so nowhere. Keyed off the seed, not off a flood: a
            # flood reported as "seed_unusable" sent the operator to edit a fine seed.
            unusable = "seed_unusable" if request.seed_channel is not None else None
            outcomes.append(skipped("telegram_similar", unusable))
        else:
            jobs.append(_seed_job(seed_handle, kind, ctx))

    keywords = sweep_keywords(request)
    sweep = _SweepState(remaining=[len(keywords)], outcomes=[], seed_handle=seed_handle)
    jobs.extend(_keyword_job(keyword, kind, sweep, ctx) for keyword in keywords)

    if any(account.premium for account in pool.accounts()):
        reservation = _PostReservation()
        pages = min(_GLOBAL_MAX_PAGES, max(1, _GLOBAL_MAX_READS // max(1, len(keywords))))
        for keyword in keywords:
            if not reservation.take():
                break
            page = _PostPage(cursor=None, seen=set(), pages_left=pages - 1)
            jobs.append(_post_job(keyword, kind, page, reservation, ctx))
    else:
        # searchGlobal answers a non-premium account with FLOOD_PREMIUM_WAIT — spending
        # a slot on it with no Premium account in the pool would only ever buy a flood.
        outcomes.append(skipped("telegram_posts", "premium_required"))

    streams = Streams(pool, work, budget=budget)
    stop = await streams.run(jobs)
    if stop is not None:
        # The run stopped for good before some retry-pending read got its one try —
        # there is no "later" for it to be settled in, so it counts as-is right now.
        outcomes.extend(ctx.pending.values())
    outcomes += streams.truncated()
    return Wave(outcomes + unreached(outcomes), stop)
