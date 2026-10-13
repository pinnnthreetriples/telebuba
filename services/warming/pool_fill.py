"""«Найти каналы» — the warming channel pool refills itself (Прогрев 2.0, item 4.5).

The operator names a language, a country and a few topics. Telegram's own search and its
"similar channels" find candidates: the same gateway reads neurocomment discovery uses,
not its campaign runtime. Each candidate must fit warming:
- between ``pool_fill_min_subscribers`` and ``pool_fill_max_subscribers`` subscribers;
- posted within ``pool_fill_alive_days``;
- not visibly in another language;
- no scam, 18+, casino or crypto-signal word in its title.

Fits are proposed as ``pending`` rows and nothing walks them until the operator
approves them in a batch (decision Q12: manual approval only).

The search reads with an account that is NOT being warmed (decision Q13): one of the
accounts neurocomment discovery may pick, preferring those handed to neurocomment.
Warming never spends its own accounts' traffic on searching.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING

from core.config import settings
from core.db import list_accounts, list_warming_channels, list_warming_states
from core.logging import log_event
from core.repositories.warming_pool import (
    approve_warming_channels,
    list_pending_warming_channels,
    propose_warming_channels,
    reject_warming_channels,
)
from core.telegram_client import TelegramReadError
from schemas._warming_pool import PoolFillResult
from schemas.telegram_actions_activity import GetLastPostAt, LastPostResult
from schemas.telegram_actions_discovery import (
    GetSimilarChannels,
    SearchChannels,
    TelegramChannelMatch,
    TelegramChannelMatches,
)
from schemas.warming import WarmingChannel
from services._lang_detect import detect_language
from services.neurocomment._discovery_pool import list_search_accounts
from services.warming import _seams

if TYPE_CHECKING:
    from schemas._warming_pool import ChannelBatchRequest, PoolFillRequest
    from schemas.warming import WarmingChannelList

# Similar-channel lookups are seeded from this many of the biggest keyword hits.
_SIMILAR_SEEDS = 2


async def _search_account() -> str | None:
    """An idle, alive account outside warming; one handed to neurocomment first."""
    free = {
        item.account_id for item in (await list_search_accounts()).items if not item.busy_reason
    }
    alive = {a.account_id for a in (await list_accounts()).accounts if a.status == "alive"}
    states = {record.account_id: record for record in await list_warming_states()}
    picks = sorted(free & alive)
    handed = [a for a in picks if (r := states.get(a)) is not None and r.nc_handed_off]
    return (handed or picks or [None])[0]


async def _matches(
    account_id: str, action: SearchChannels | GetSimilarChannels
) -> list[TelegramChannelMatch]:
    try:
        result = await _seams.execute_read(account_id, action)
    except TelegramReadError as exc:
        await log_event(
            "WARNING",
            "warming_pool_fill_read_failed",
            account_id=account_id,
            extra={"error_type": exc.kind},
        )
        return []
    return result.items if isinstance(result, TelegramChannelMatches) else []


def _blocked(title: str) -> bool:
    lowered = title.casefold()
    return any(word.casefold() in lowered for word in settings.warming.pool_fill_blocked_words)


def _fits(match: TelegramChannelMatch, language: str, known: set[str]) -> bool:
    """The free checks, before any probe: a handle, size, title words, title language."""
    warm = settings.warming
    if not match.username or f"@{match.username}".casefold() in known:
        return False
    size = match.participants_count
    if (
        size is not None
        and not warm.pool_fill_min_subscribers <= size <= warm.pool_fill_max_subscribers
    ):
        return False
    title_language = detect_language(match.title)
    return not _blocked(match.title) and title_language in {None, language}


async def _alive_since(account_id: str, handle: str, now: datetime) -> str | None:
    """The newest post's time when it is recent enough, else ``None``."""
    try:
        result = await _seams.execute_read(account_id, GetLastPostAt(channel=handle))
    except TelegramReadError:
        return None
    if not isinstance(result, LastPostResult) or result.last_post_at is None:
        return None
    posted = datetime.fromisoformat(result.last_post_at)
    posted = posted if posted.tzinfo is not None else posted.replace(tzinfo=UTC)
    alive = now - posted <= timedelta(days=settings.warming.pool_fill_alive_days)
    return result.last_post_at if alive else None


async def _collect(account_id: str, request: PoolFillRequest) -> list[TelegramChannelMatch]:
    hits: dict[str, TelegramChannelMatch] = {}
    for topic in request.topics:
        for match in await _matches(account_id, SearchChannels(query=topic, kind="all")):
            hits.setdefault((match.username or "").casefold(), match)
    seeds = sorted(
        (m for m in hits.values() if m.username),
        key=lambda m: m.participants_count or 0,
        reverse=True,
    )[:_SIMILAR_SEEDS]
    for seed in seeds:
        for match in await _matches(account_id, GetSimilarChannels(seed=f"@{seed.username}")):
            hits.setdefault((match.username or "").casefold(), match)
    return [match for key, match in hits.items() if key]


async def find_channels(request: PoolFillRequest) -> PoolFillResult:
    """Search, check and propose channels for the pool; the operator approves them later."""
    account_id = await _search_account()
    if account_id is None:
        return PoolFillResult(status="no_search_account")
    pool = (await list_warming_channels()).channels + (
        await list_pending_warming_channels()
    ).channels
    known = {channel.channel.casefold() for channel in pool}
    found = await _collect(account_id, request)
    fitting = [m for m in found if _fits(m, request.language, known)]
    now = datetime.now(UTC)
    proposals: list[WarmingChannel] = []
    checked = fitting[: settings.warming.pool_fill_max_probes]
    for match in checked:
        handle = f"@{match.username}"
        last_post = await _alive_since(account_id, handle, now)
        if last_post is None:
            continue
        proposals.append(
            WarmingChannel(
                channel=handle,
                label=match.title or None,
                created_at=now.isoformat(),
                language=request.language,
                country=request.country,
                kind=match.kind,
                subscribers=match.participants_count,
                last_post_at=last_post,
                source="discovery",
                status="pending",
            ),
        )
    proposed = await propose_warming_channels(proposals)
    await log_event(
        "INFO",
        "warming_pool_fill_done",
        account_id=account_id,
        extra={"found": len(found), "checked": len(checked), "proposed": proposed},
    )
    return PoolFillResult(status="ok", found=len(found), checked=len(checked), proposed=proposed)


async def list_proposals() -> WarmingChannelList:
    """The proposals waiting for the operator."""
    return await list_pending_warming_channels()


async def approve_proposals(data: ChannelBatchRequest) -> WarmingChannelList:
    """Move the ticked proposals into the walked pool."""
    approved = await approve_warming_channels(data.channels)
    await log_event("INFO", "warming_pool_fill_approved", extra={"count": approved})
    return await list_pending_warming_channels()


async def reject_proposals(data: ChannelBatchRequest) -> WarmingChannelList:
    """Drop the ticked proposals."""
    rejected = await reject_warming_channels(data.channels)
    await log_event("INFO", "warming_pool_fill_rejected", extra={"count": rejected})
    return await list_pending_warming_channels()
