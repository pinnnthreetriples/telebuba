"""«Найти каналы» and the automatic refill — a pool search with an account outside warming.

The search looks for channels of one language (and country) on the given topics, or
on the interests of that language's accounts when none are given (``_pool_topics``).
Everything that passes ``_pool_filters`` goes straight into the pool as ``approved``:
there is no approval step any more, the operator prunes the list instead.

**Which account reads.** A free one, when there is one (``_pool_accounts``). Otherwise
nothing is taken on the search's own initiative: the result is ``needs_consent`` with
the busy account the search would take and what it is doing, and only a second call
carrying that account as ``pull_account_id`` — the operator's «Выдернуть на поиск» —
takes it. A warming account is never taken, not even then.

**While it reads, the account is the search's.** The account's lifecycle lock is held
for the whole search, and the search lends it in ``services._account_owner`` on top of
whatever holds it. Every feature claims accounts and dispatches under that same lock,
so nothing can claim it or send with it until the search ends; the loan is what their
pickers and per-turn checks see meanwhile. The loan is ended in ``finally``, so a
search that fails, times out (``pool_fill_timeout_seconds``) or is cancelled still
gives the account back to whatever held it before.

**One pool search at a time.** A second one, from the button or the refill, is told
``already_running`` instead of queueing another account's reads behind the first.
"""

from __future__ import annotations

import asyncio
import uuid
from datetime import UTC, datetime
from typing import TYPE_CHECKING

from core.config import settings
from core.logging import log_event
from core.repositories.warming_pool import add_found_warming_channels, list_known_warming_channels
from core.repositories.warming_searches import record_channel_search
from schemas._warming_pool import ChannelSearchRecord, PoolFillResult, PoolSearchCandidate
from services import _account_owner
from services.neurocomment._discovery_providers import flood_cooldown, record_flood
from services.warming import _seams
from services.warming._pool_accounts import free_search_accounts, pull_candidates, search_standing
from services.warming._pool_hunt import HuntPlan, hunt_channels
from services.warming._pool_topics import topics_for
from services.warming._runtime import account_lock
from services.warming._steps import _human_delay

if TYPE_CHECKING:
    from pydantic import BaseModel

    from schemas._warming_pool import ChannelSearchOrigin, PoolFillRequest
    from schemas.telegram_actions import TelegramReadAction
    from services.warming._pool_hunt import HuntOutcome, Reader

_SIMILAR_SEEDS = 2


class _Running:
    """Whether a pool search is under way; set and cleared with no await in between."""

    active = False


async def find_channels(
    request: PoolFillRequest, origin: ChannelSearchOrigin = "manual"
) -> PoolFillResult:
    """Search with a free account, or with the pulled one the operator agreed to give."""
    if _Running.active:
        return PoolFillResult(status="already_running")
    _Running.active = True
    try:
        return await _find(request, origin)
    finally:
        _Running.active = False


def is_running() -> bool:
    """Whether a pool search is reading right now."""
    return _Running.active


async def _find(request: PoolFillRequest, origin: ChannelSearchOrigin) -> PoolFillResult:
    topics = request.topics or await topics_for(request.language)
    if not topics:
        return PoolFillResult(status="no_topics")
    if request.pull_account_id is not None:
        return await _pull(request.pull_account_id, request, topics, origin)
    for account_id in await free_search_accounts():
        result = await _search_locked(account_id, None, request, topics, origin)
        if result is not None:
            return result
    candidates = await pull_candidates()
    if candidates:
        return PoolFillResult(status="needs_consent", pull_candidate=candidates[0])
    return PoolFillResult(status="no_search_account")


async def _pull(
    account_id: str, request: PoolFillRequest, topics: list[str], origin: ChannelSearchOrigin
) -> PoolFillResult:
    """The consented pull: only while the account still does what the operator was told."""
    standing = await search_standing(account_id)
    if standing is None:
        return PoolFillResult(status="no_search_account")
    consent = standing if isinstance(standing, PoolSearchCandidate) else None
    result = await _search_locked(account_id, consent, request, topics, origin)
    if result is not None:
        return result
    fresh = await search_standing(account_id)
    if isinstance(fresh, PoolSearchCandidate):
        return PoolFillResult(status="needs_consent", pull_candidate=fresh)
    return PoolFillResult(status="no_search_account")


def _same_standing(
    standing: PoolSearchCandidate | str | None, consent: PoolSearchCandidate | None
) -> bool:
    if consent is None:
        return isinstance(standing, str)
    return isinstance(standing, PoolSearchCandidate) and standing.busy_with == consent.busy_with


async def _search_locked(
    account_id: str,
    consent: PoolSearchCandidate | None,
    request: PoolFillRequest,
    topics: list[str],
    origin: ChannelSearchOrigin,
) -> PoolFillResult | None:
    """Borrow the account under its lock and search; ``None`` when it is no longer ours.

    The standing is asked again once the lock is held — the pick is several awaits old
    — and the claim beneath the loan is compared once more right after lending, in the
    same await-free stretch, so a claim that landed meanwhile is never searched over.
    """
    async with account_lock(account_id):
        standing = await search_standing(account_id)
        if not _same_standing(standing, consent):
            return None
        beneath = _account_owner.owners().get(account_id)
        holder = uuid.uuid4().hex
        if not _account_owner.lend(account_id, holder):
            return None
        try:
            if _account_owner.owners().get(account_id) != beneath:
                return None
            if consent is not None:
                await _log_loan("warming_pool_search_account_taken", account_id, consent)
            return await _search_with(account_id, request, topics, origin)
        finally:
            _account_owner.give_back(account_id, holder)
            if consent is not None:
                await _log_loan("warming_pool_search_account_returned", account_id, consent)


async def _log_loan(code: str, account_id: str, consent: PoolSearchCandidate) -> None:
    await log_event("INFO", code, account_id=account_id, extra={"busy_with": consent.busy_with})


def _reader(account_id: str) -> Reader:
    """Reads with ``account_id``, a person's pause before every read but the first."""
    first = True

    async def read(action: TelegramReadAction) -> BaseModel:
        nonlocal first
        if not first:
            await _seams.sleep(_human_delay(*settings.warming.pool_fill_pause_seconds))
        first = False
        return await _seams.execute_read(account_id, action)

    return read


async def _hunt(account_id: str, request: PoolFillRequest, topics: list[str]) -> HuntOutcome | None:
    """The search itself, bounded in time; ``None`` when it ran out of time."""
    warm = settings.warming
    plan = HuntPlan(
        language=request.language,
        country=request.country,
        topics=tuple(topics),
        source="discovery",
        max_probes=warm.pool_fill_max_probes,
        max_reads=len(topics) + _SIMILAR_SEEDS + 2 * warm.pool_fill_max_probes,
        similar_seeds=_SIMILAR_SEEDS,
    )
    known = await list_known_warming_channels()
    try:
        async with asyncio.timeout(warm.pool_fill_timeout_seconds):
            return await hunt_channels(_reader(account_id), plan, known)
    except TimeoutError:
        return None


async def _search_with(
    account_id: str, request: PoolFillRequest, topics: list[str], origin: ChannelSearchOrigin
) -> PoolFillResult:
    search = ChannelSearchRecord(
        origin=origin,
        account_id=account_id,
        language=request.language,
        status="ok",
        created_at=datetime.now(UTC).isoformat(),
    )
    outcome = await _hunt(account_id, request, topics)
    if outcome is None:
        await record_channel_search(search.model_copy(update={"status": "failed"}))
        return PoolFillResult(status="failed")
    added = await add_found_warming_channels(outcome.fits, settings.warming.max_channels_total)
    if outcome.flood is not None:
        search.status = "flood"
        await record_flood(account_id, flood_cooldown(outcome.flood))
    search.found, search.added = outcome.found, len(added)
    await record_channel_search(search)
    await log_event(
        "WARNING" if outcome.flood is not None else "INFO",
        "warming_pool_fill_done",
        account_id=account_id,
        extra={
            "origin": origin,
            "found": outcome.found,
            "checked": outcome.checked,
            "added": len(added),
            "flooded": outcome.flood is not None,
        },
    )
    return PoolFillResult(
        status="flooded" if outcome.flood is not None else "ok",
        found=outcome.found,
        checked=outcome.checked,
        added=len(added),
    )
