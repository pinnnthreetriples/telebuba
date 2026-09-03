"""Channel-discovery sources: the Telegram adapters and the account health signals.

``core`` exposes typed read actions; dedup order, merge policy and which account
does the reading are business logic and live here (the account choice itself in
``_discovery_pool``). ``services/`` therefore never imports telethon.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING

from core.config import settings
from core.telegram_client import TelegramReadError
from schemas.telegram_actions import GetSimilarChannels, SearchChannels, SearchGlobalPosts
from schemas.telegram_actions_discovery import (
    TelegramChannelMatches,
    TelegramGlobalPostMatches,
)
from services.neurocomment import _seams
from services.neurocomment._discovery_filters import access_of, private_ref
from services.neurocomment._state import in_cooldown, set_cooldown

if TYPE_CHECKING:
    from schemas.neurocomment_discovery import DiscoverySource, DiscoverySourceState
    from schemas.telegram_actions_discovery import DiscoveryKind, GlobalPostsCursor


# Short locale-neutral reason for a run stopped mid-flight because the account is
# serving a limit. Spelled like the start status it mirrors, so the operator reads the
# same words whether the account was cooling before the run or became so during it.
COOLING_REASON = "account_cooling"


def account_cooling(account_id: str) -> bool:
    """Is a live cooldown in force on this account right now?

    The mid-run half of the health gate ``check_search_accounts`` applies once at the
    start. A run is minutes long, and the comment engine (or the run's own later reads)
    can park the account at any point in it; every read after that lands inside a live
    window, which is how Telegram turns a soft limit into a hard one.

    Two dict lookups, no Telegram RPC and no database round trip — asking Telegram
    whether it is rate-limiting us costs exactly the read the check exists to prevent.
    Warming's persisted flood deadline is deliberately NOT re-read here: warming cannot
    start on this account while the run holds it (``services.warming._exclusion``), so
    that deadline can no longer change under a run that already passed the start gate.
    """
    return in_cooldown(account_id, datetime.now(UTC))


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

    username: str | None
    title: str
    subscribers: int | None
    source: DiscoverySource
    kind: str = "channel"
    # ``None`` = public handle, join gate unknown until the probe.
    access: str | None = None
    # Recommendations may return a private channel: no handle, so the id addresses it.
    channel_id: int | None = None

    @property
    def ref(self) -> str:
        """What the row is stored under: the handle, or ``id:<n>`` when there is none."""
        return self.username or private_ref(self.channel_id)


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
                kind=item.kind,
                access=access_of(item.username, item.join_request),
                channel_id=item.channel_id,
            )
            for item in result.items
        ),
    )


async def search_native(
    account_id: str,
    keyword: str,
    kind: DiscoveryKind = "channels",
) -> SourceOutcome:
    """Telegram's own channel (and/or group) search for one keyword."""
    try:
        result = await _seams.execute_read(
            account_id,
            SearchChannels(query=keyword, kind=kind),
        )
    except TelegramReadError as exc:
        return _failed("telegram_search", exc)
    return _matches_outcome(result, "telegram_search")


async def search_similar(
    account_id: str,
    seed: str,
    source: DiscoverySource = "telegram_similar",
    kind: DiscoveryKind = "all",
) -> SourceOutcome:
    """Channels similar to a seed — the cheapest way to widen a sweep.

    ``source`` names which wave asked. The operator's own seed reports as
    ``telegram_similar``; the wave that re-asks around the keyword sweep's best hits
    reports as ``telegram_recommended``. Same RPC, two report rows on purpose: folded
    into one, a wave that answered would flip the row to ``ran`` and bury the seed's
    ``seed_unusable``.
    """
    try:
        result = await _seams.execute_read(account_id, GetSimilarChannels(seed=seed, kind=kind))
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
    kind: DiscoveryKind = "channels",
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
            SearchGlobalPosts(query=keyword, cursor=cursor, kind=kind),
        )
    except TelegramReadError as exc:
        return GlobalPage(_failed("telegram_posts", exc))
    return GlobalPage(
        _matches_outcome(result, "telegram_posts"),
        result.next_cursor if isinstance(result, TelegramGlobalPostMatches) else None,
    )
