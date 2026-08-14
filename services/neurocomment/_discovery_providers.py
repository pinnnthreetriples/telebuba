"""Channel-discovery sources: the Telegram adapters plus the account choice.

``core`` exposes typed read actions; dedup order, merge policy and which account
does the reading are business logic and live here. ``services/`` therefore never
imports telethon.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING

from core.config import settings
from core.db import fetch_warming_state, list_warming_account_ids
from core.repositories.neurocomment import (
    get_listener_account_id,
    get_listener_running,
    list_campaign_accounts,
)
from core.telegram_client import TelegramReadError
from schemas.telegram_actions import GetSimilarChannels, SearchChannels, SearchGlobalPosts
from schemas.telegram_actions_discovery import (
    TelegramChannelMatches,
    TelegramGlobalPostMatches,
)
from services.neurocomment import _seams
from services.neurocomment._state import in_cooldown, set_cooldown
from services.trust import flood_active

if TYPE_CHECKING:
    from schemas.neurocomment_discovery import DiscoverySource, DiscoverySourceState
    from schemas.telegram_actions_discovery import GlobalPostsCursor


def flood_cooldown(exc: TelegramReadError) -> int | None:
    """How long this read failure must park the account, or ``None`` — it was no limit.

    The gateway's own ``kind`` is the classification; re-reading the string it just
    formatted is what made discovery narrower than the rest of the fleet. Only
    ``FloodWaitError`` renders as ``FloodWait(<n>s)``, while ``FLOOD_PREMIUM_WAIT`` (the
    reply a non-premium account genuinely gets), ``SLOW_MODE_WAIT`` and ``PEER_FLOOD``
    are siblings of it — so they matched nothing, no cooldown was written, and every
    remaining read of the run fired into a live limit. Same family
    ``services.neurocomment._classify`` and the write gateway already treat as one.

    A limit with no duration on the wire (peer flood) takes the config default the
    comment engine's own cooldown already applies to exactly that case.
    """
    if exc.kind != "flood_wait":
        return None
    if exc.seconds is None:
        return int(settings.neurocomment.peer_flood_cooldown_seconds)
    return exc.seconds


@dataclass(frozen=True, slots=True)
class RawCandidate:
    """One provider hit, before normalization and cross-source dedup."""

    username: str
    title: str
    subscribers: int | None
    source: DiscoverySource


@dataclass(frozen=True, slots=True)
class SourceOutcome:
    """What one provider produced, plus a short reason when it degraded.

    A failing source must never abort the run — the other source's results still
    have value — so the reason is data, not an exception. ``source`` is carried even
    when there are no candidates: a failed source has none, and the board has to be
    able to name which one it was.
    """

    source: DiscoverySource
    state: DiscoverySourceState = "ran"
    candidates: tuple[RawCandidate, ...] = ()
    error: str | None = None
    # Seconds this failure must park the account for, ``None`` when it was not a rate
    # limit at all. Carried rather than re-derived from ``error``: only the gateway knows
    # WHICH limit landed, and the whole family reads back as one opaque string.
    flood_seconds: int | None = None
    # Set on the placeholder outcome a wave appends when the run's shared read budget
    # left it a read short, so the board can separate "this is all there was" from
    # "we stopped asking".
    truncated: bool = False

    @property
    def answered(self) -> bool:
        """Did this source actually return a result?

        False for a skipped source (no seed) and for a failed one. It
        separates "nobody answered" from "the answers came back empty", which is what
        decides whether an empty merge may replace the stored candidate set.
        """
        return self.state == "ran"


@dataclass(frozen=True, slots=True)
class SearchAccount:
    """The single account a run uses for every Telegram read it makes."""

    account_id: str


async def resolve_search_account(campaign_id: str) -> SearchAccount | str:
    """Pick the account that will search and qualify, or return a refusal status.

    Prefers the listener: it is already the fleet's designated read-only account
    (it resolves peers and subscribes, never comments), so discovery traffic stays
    off the commenting accounts. Falls back to the campaign's first serving account.

    Returns a status string instead of raising, so the API layer can report it
    without catching service internals: ``"no_account"``, ``"account_busy"`` (the
    session is held by a running listener or by warming) or ``"account_cooling"``
    (Telegram is rate-limiting the account).
    """
    listener_id = await get_listener_account_id()
    account_id = listener_id
    if account_id is None:
        links = await list_campaign_accounts(campaign_id)
        account_id = links.links[0].account_id if links.links else None
    if account_id is None:
        return "no_account"

    # The listener is preferred precisely because it is read-only, but a *running* one
    # holds the session and reads continuously. Layering a multi-minute paced keyword
    # stream plus up to 100 probes on top of it is the same mutual-exclusion violation
    # the warming check below prevents, and the listener is routinely running.
    if account_id == listener_id and await get_listener_running():
        return "account_busy"

    now = datetime.now(UTC)
    # Two independent health signals: the engine's in-memory cooldown (flood /
    # peer-flood / slow-mode) and warming's persisted flood deadline. Searching on a
    # cooling account would deepen the very limit it is serving out.
    # Ahead of the warming check below: a warming account can also be flood-waiting,
    # and both refuse, so the order only picks which reason the operator is told.
    if in_cooldown(account_id, now):
        return "account_cooling"
    state = await fetch_warming_state(account_id)
    if state is not None and flood_active(state.flood_wait_until, now):
        return "account_cooling"

    # Warming assumes it owns its accounts' traffic — that assumption is the whole
    # basis of its freeze avoidance. Every other listener consumer enforces the same
    # mutual exclusion; a paused listener can legally be warming, so without this a
    # multi-minute read stream would interleave with warming's own paced traffic.
    if account_id in await list_warming_account_ids():
        return "account_busy"
    return SearchAccount(account_id=account_id)


async def record_flood(account_id: str, seconds: int | None) -> bool:
    """Register a discovery-caused rate limit as a cooldown; says whether it was one.

    Discovery reads both fleet flood signals but wrote neither, so its own limit was
    invisible to everyone else: the operator's immediate retry passed the health gate,
    and the reconcile that follows an adopt would run peer resolution and joins on a
    flooded account — leaving the engine deaf on the channels just added.
    """
    if seconds is None:
        return False
    await set_cooldown(account_id, datetime.now(UTC) + timedelta(seconds=seconds))
    return True


def _failed(source: DiscoverySource, exc: TelegramReadError) -> SourceOutcome:
    """A source the gateway refused, carrying the cooldown the refusal earns."""
    return SourceOutcome(
        source=source,
        state="failed",
        error=exc.reason,
        flood_seconds=flood_cooldown(exc),
    )


def _matches_outcome(result: object, source: DiscoverySource) -> SourceOutcome:
    """Narrow the gateway's reply, treating an unknown shape as a failure.

    Counting it as an answer with zero candidates would let a Telethon-layer change
    authorise the wholesale replace and wipe the stored candidate set.
    """
    if not isinstance(result, TelegramChannelMatches):
        return SourceOutcome(source=source, state="failed", error="unexpected_result")
    return SourceOutcome(
        source=source,
        candidates=tuple(
            RawCandidate(
                username=item.username,
                title=item.title,
                subscribers=item.participants_count,
                source=source,
            )
            for item in result.items
        ),
    )


async def search_native(account_id: str, keyword: str) -> SourceOutcome:
    """Telegram's own channel search for one keyword."""
    try:
        result = await _seams.execute_read(
            account_id,
            SearchChannels(query=keyword),
        )
    except TelegramReadError as exc:
        return _failed("telegram_search", exc)
    return _matches_outcome(result, "telegram_search")


async def search_similar(
    account_id: str,
    seed: str,
    source: DiscoverySource = "telegram_similar",
) -> SourceOutcome:
    """Channels similar to a seed — the cheapest way to widen a sweep.

    ``source`` names which wave asked. The operator's own seed reports as
    ``telegram_similar``; the wave that re-asks around the keyword sweep's best hits
    reports as ``telegram_recommended``. Same RPC, two report rows on purpose: folded
    into one, a wave that answered would flip the row to ``ran`` and bury the seed's
    ``seed_unusable``.
    """
    try:
        result = await _seams.execute_read(account_id, GetSimilarChannels(seed=seed))
    except TelegramReadError as exc:
        return _failed(source, exc)
    return _matches_outcome(result, source)


@dataclass(frozen=True, slots=True)
class GlobalPage:
    """One page of the global post search, and where to continue it.

    ``cursor`` is ``None`` when the page carried no message to continue from, or when
    the read failed. It is NOT an end-of-results flag — Telegram never sends one, and
    ``limit`` counts messages rather than channels — so the caller bounds its own paging.
    """

    outcome: SourceOutcome
    cursor: GlobalPostsCursor | None = None


async def search_global(
    account_id: str,
    keyword: str,
    cursor: GlobalPostsCursor | None = None,
) -> GlobalPage:
    """One page of channels whose POSTS match a keyword (``messages.searchGlobal``).

    A second index, not a replacement for the keyword search: core.telegram.org
    documents this method only as "search for messages and peers globally", while
    ``channels.searchPosts`` is the one documented as covering channels we are not a
    member of. Treated as a useful extra source with a small page budget.
    """
    try:
        result = await _seams.execute_read(
            account_id,
            SearchGlobalPosts(query=keyword, cursor=cursor),
        )
    except TelegramReadError as exc:
        return GlobalPage(_failed("telegram_posts", exc))
    return GlobalPage(
        _matches_outcome(result, "telegram_posts"),
        result.next_cursor if isinstance(result, TelegramGlobalPostMatches) else None,
    )
