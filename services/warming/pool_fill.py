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

from core.channel_tokens import dedup_key
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
from schemas.telegram_action_results import LinkedDiscussionGroupResult
from schemas.telegram_actions import GetLinkedDiscussionGroup
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
from services.warming._runtime import account_lock

if TYPE_CHECKING:
    from schemas._warming_pool import ChannelBatchRequest, PoolFillRequest
    from schemas.warming import WarmingChannelList

# Similar-channel lookups are seeded from this many of the biggest keyword hits.
_SIMILAR_SEEDS = 2


async def _search_accounts() -> list[str]:
    """Idle, alive accounts outside warming, those handed to neurocomment first."""
    free = {
        item.account_id for item in (await list_search_accounts()).items if not item.busy_reason
    }
    alive = {a.account_id for a in (await list_accounts()).accounts if a.status == "alive"}
    states = {record.account_id: record for record in await list_warming_states()}
    picks = sorted(free & alive)
    handed = [a for a in picks if (r := states.get(a)) is not None and r.nc_handed_off]
    return handed + [a for a in picks if a not in handed]


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


def _size_ok(size: int | None) -> bool:
    warm = settings.warming
    return (
        size is not None
        and warm.pool_fill_min_subscribers <= size <= warm.pool_fill_max_subscribers
    )


def _text_ok(text: str, language: str) -> bool:
    """No blocked word, and not visibly written in another language."""
    return not _blocked(text) and detect_language(text) in {None, language}


def _fits(match: TelegramChannelMatch, language: str, known: set[str]) -> bool:
    """The free checks, before any probe: a new handle, the title, a size if one came."""
    if not match.username or dedup_key(match.username) in known:
        return False
    size = match.participants_count
    return _text_ok(match.title, language) and (size is None or _size_ok(size))


async def _probe(
    account_id: str,
    match: TelegramChannelMatch,
    language: str,
    now: datetime,
) -> tuple[int, str] | None:
    """``(subscribers, last post)`` of a channel that fits on its full info, else ``None``.

    Search hits rarely carry a subscriber count, so ``getFullChannel`` answers it, with
    the about text the words and the language are checked against too.
    """
    handle = f"@{match.username}"
    try:
        full = await _seams.execute_read(account_id, GetLinkedDiscussionGroup(channel=handle))
    except TelegramReadError:
        return None
    if not isinstance(full, LinkedDiscussionGroupResult):
        return None
    size = (
        full.participants_count if full.participants_count is not None else match.participants_count
    )
    text = " ".join(part for part in (match.title, full.about or "") if part)
    if not _size_ok(size) or not _text_ok(text, language) or size is None:
        return None
    last_post = await _alive_since(account_id, handle, now)
    return (size, last_post) if last_post is not None else None


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
    """Search, check and propose channels for the pool; the operator approves them later.

    The searching account's lifecycle lock is held for the whole read sequence, and the
    account is checked again once it is held: warming starts take the same lock, so it
    cannot claim the account while the search still reads with it.
    """
    for account_id in await _search_accounts():
        async with account_lock(account_id):
            if account_id in await _search_accounts():
                return await _search_with(account_id, request)
    return PoolFillResult(status="no_search_account")


async def _search_with(account_id: str, request: PoolFillRequest) -> PoolFillResult:
    pool = (await list_warming_channels()).channels + (
        await list_pending_warming_channels()
    ).channels
    known = {dedup_key(channel.channel) for channel in pool}
    found = await _collect(account_id, request)
    fitting = [m for m in found if _fits(m, request.language, known)]
    now = datetime.now(UTC)
    proposals: list[WarmingChannel] = []
    checked = fitting[: settings.warming.pool_fill_max_probes]
    for match in checked:
        probed = await _probe(account_id, match, request.language, now)
        if probed is None:
            continue
        subscribers, last_post = probed
        proposals.append(
            WarmingChannel(
                channel=dedup_key(match.username or ""),
                label=match.title or None,
                created_at=now.isoformat(),
                language=request.language,
                country=request.country,
                kind=match.kind,
                subscribers=subscribers,
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
