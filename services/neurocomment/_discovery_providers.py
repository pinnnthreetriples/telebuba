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

from dataclasses import dataclass
from datetime import UTC, datetime
from typing import TYPE_CHECKING

from core.config import settings
from core.db import fetch_warming_state, load_warming_settings
from core.repositories.neurocomment import get_listener_account_id, list_campaign_accounts
from core.telegram_client import TelegramReadError
from schemas.telegram_actions import GetSimilarChannels, SearchChannels
from schemas.telegram_actions_discovery import TelegramChannelMatches
from schemas.telemetr import TelemetrSearchRequest
from services.neurocomment import _seams
from services.neurocomment._state import in_cooldown
from services.trust import flood_active

if TYPE_CHECKING:
    from schemas.neurocomment_discovery import DiscoverySearchRequest, DiscoverySource


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
    have value — so the reason is data, not an exception.
    """

    candidates: tuple[RawCandidate, ...] = ()
    error: str | None = None


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
    account_id = await get_listener_account_id()
    if account_id is None:
        links = await list_campaign_accounts(campaign_id)
        account_id = links.links[0].account_id if links.links else None
    if account_id is None:
        return "no_account"

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


def _matches_to_candidates(
    result: object,
    source: DiscoverySource,
) -> tuple[RawCandidate, ...]:
    if not isinstance(result, TelegramChannelMatches):  # pragma: no cover - typed gateway
        return ()
    return tuple(
        RawCandidate(
            username=item.username,
            title=item.title,
            subscribers=item.participants_count,
            source=source,
        )
        for item in result.items
    )


async def search_native(account_id: str, keyword: str) -> SourceOutcome:
    """Telegram's own channel search for one keyword."""
    try:
        result = await _seams.execute_read(
            account_id,
            SearchChannels(query=keyword),
        )
    except TelegramReadError as exc:
        return SourceOutcome(error=exc.reason)
    return SourceOutcome(candidates=_matches_to_candidates(result, "telegram_search"))


async def search_similar(account_id: str, seed: str | None) -> SourceOutcome:
    """Channels similar to a seed — the cheapest way to widen a sweep."""
    try:
        result = await _seams.execute_read(account_id, GetSimilarChannels(seed=seed))
    except TelegramReadError as exc:
        return SourceOutcome(error=exc.reason)
    return SourceOutcome(candidates=_matches_to_candidates(result, "telegram_similar"))


async def search_telemetr(keyword: str, request: DiscoverySearchRequest) -> SourceOutcome:
    """The external catalogue: country/language filters plus subscriber counts.

    A missing key is a *skipped* source, not an error — the operator simply has not
    configured it, and the native source still ran.
    """
    secret = await load_warming_settings()
    result = await _seams.search_telemetr(
        TelemetrSearchRequest(
            api_key=secret.telemetr_api_key,
            term=keyword,
            country=request.country,
            language=request.language,
            members_min=request.members_min,
            members_max=request.members_max,
            limit=settings.telemetr.search_limit,
        ),
    )
    if result.status == "not_configured":
        return SourceOutcome()
    if result.status != "ok":
        return SourceOutcome(error=f"telemetr_{result.status}")
    return SourceOutcome(
        candidates=tuple(
            RawCandidate(
                username=item.username,
                title=item.title,
                subscribers=item.members_count,
                source="telemetr",
            )
            for item in result.items
        ),
    )
