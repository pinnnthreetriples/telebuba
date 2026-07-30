"""Channel-discovery sources: a protocol plus the two adapters behind it.

The abstraction lives here rather than in ``core/`` on purpose. ``core.gemini`` /
``core.openai`` set the precedent: two dumb gateways over a shared contract, with
the *choice* between them resolved as service policy. A core-level abstraction
would have to know about the campaign, the operator's key, dedup order and merge
policy — all business logic. So ``core`` exposes typed actions plus a typed HTTP
result, and this module owns the protocol, the adapters and the account choice.
``services/`` therefore never imports telethon or httpx.
"""

from __future__ import annotations

import re
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
from schemas.telegram_actions import GetSimilarChannels, SearchChannels
from schemas.telegram_actions_discovery import TelegramChannelMatches
from schemas.telemetr import TelemetrSearchRequest
from services.neurocomment import _seams
from services.neurocomment._state import in_cooldown, set_cooldown
from services.trust import flood_active

if TYPE_CHECKING:
    from schemas.neurocomment_discovery import (
        DiscoverySearchRequest,
        DiscoverySource,
        DiscoverySourceState,
    )

# The gateway renders a flood wait as ``FloodWait(<seconds>s)`` (core.telegram_client).
_FLOOD_SECONDS = re.compile(r"FloodWait\((\d+)s\)")


@dataclass(frozen=True, slots=True)
class RawCandidate:
    """One provider hit, before normalization and cross-source dedup."""

    username: str
    title: str
    subscribers: int | None
    source: DiscoverySource
    # Only the catalogue knows these; they ride through to the board so a filter can be
    # verified rather than trusted.
    country: str | None = None
    language: str | None = None


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
    # The gateway's own diagnostic text, kept apart from the short code above so the
    # board can show one and the operator can act on the other.
    detail: str | None = None

    @property
    def answered(self) -> bool:
        """Did this source actually return a result?

        False for a skipped source (disabled, no key, no seed) and for a failed one. It
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

    Returns the status string ``"no_account"`` / ``"account_cooling"`` instead of
    raising, so the API layer can report it without catching service internals.
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
        return "account_cooling"

    # Warming assumes it owns its accounts' traffic — that assumption is the whole
    # basis of its freeze avoidance. Every other listener consumer enforces the same
    # mutual exclusion; a paused listener can legally be warming, so without this a
    # multi-minute read stream would interleave with warming's own paced traffic.
    if account_id in await list_warming_account_ids():
        return "account_cooling"

    now = datetime.now(UTC)
    # Two independent health signals: the engine's in-memory cooldown (flood /
    # peer-flood / slow-mode) and warming's persisted flood deadline. Searching on a
    # cooling account would deepen the very limit it is serving out.
    if in_cooldown(account_id, now):
        return "account_cooling"
    state = await fetch_warming_state(account_id)
    if state is not None and flood_active(state.flood_wait_until, now):
        return "account_cooling"
    return SearchAccount(account_id=account_id)


async def record_flood(account_id: str, reason: str | None) -> bool:
    """Register a discovery-caused FloodWait as a cooldown; says whether it was one.

    Discovery reads both fleet flood signals but wrote neither, so its own limit was
    invisible to everyone else: the operator's immediate retry passed the health gate,
    and the reconcile that follows an adopt would run peer resolution and joins on a
    flooded account — leaving the engine deaf on the channels just added.
    """
    match = _FLOOD_SECONDS.search(reason or "")
    if match is None:
        return False
    await set_cooldown(account_id, datetime.now(UTC) + timedelta(seconds=int(match.group(1))))
    return True


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
        return SourceOutcome(source="telegram_search", state="failed", error=exc.reason)
    return _matches_outcome(result, "telegram_search")


async def search_similar(account_id: str, seed: str) -> SourceOutcome:
    """Channels similar to a seed — the cheapest way to widen a sweep."""
    try:
        result = await _seams.execute_read(account_id, GetSimilarChannels(seed=seed))
    except TelegramReadError as exc:
        return SourceOutcome(source="telegram_similar", state="failed", error=exc.reason)
    return _matches_outcome(result, "telegram_similar")


async def search_telemetr(
    keyword: str,
    request: DiscoverySearchRequest,
    api_key: str,
) -> SourceOutcome:
    """The external catalogue: country/language filters plus subscriber counts.

    A missing key is a *skipped* source rather than a failure — but it is still
    reported: the operator ticked the box and the catalogue was never queried, and
    saying nothing let the run reach "done" as if the filter had applied.

    ``api_key`` is passed in, not read here: the read decrypts a secret and this runs
    once per keyword.
    """
    result = await _seams.search_telemetr(
        TelemetrSearchRequest(
            api_key=api_key,
            term=keyword,
            country=request.country,
            language=request.language,
            members_min=request.members_min,
            members_max=request.members_max,
            limit=settings.telemetr.search_limit,
        ),
    )
    if result.status != "ok":
        # ``detail`` is the gateway's own text, which distinguishes a revoked key from an
        # expired subscription from a rejected filter value from a dead network — the
        # short code cannot. It carries no part of the API key (core.telemetr scrubs it),
        # so it is safe to show and to log, and the run's finish event logs it.
        return SourceOutcome(
            source="telemetr",
            state="skipped" if result.status == "not_configured" else "failed",
            error=f"telemetr_{result.status}",
            detail=result.error,
        )
    return SourceOutcome(
        source="telemetr",
        candidates=tuple(
            RawCandidate(
                username=item.username,
                title=item.title,
                subscribers=item.members_count,
                source="telemetr",
                country=item.country,
                language=item.language,
            )
            for item in result.items
        ),
    )
