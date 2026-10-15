"""A warming account looks for channels itself, inside its own visit (Прогрев 2.0 follow-up).

Real people find channels by searching for what they are into. So an account that needs
channels — its language is short in the pool, or it has fewer than
``self_search_target_found`` channels it found itself — sometimes types one of its
character's interests (4.2) into Telegram's search, glances at the results and a few
"similar channels", opens two or three of them, and joins the ones it likes. What fits
the same filters as any pool search goes into the shared pool at once (``self_search``,
``found_by`` this account), so every account of the language gains from it, and this
account walks its finds first in this visit and keeps them in its interests after.

**When.** Only from ``self_search_min_phase`` (default ``warming``): the first days
(``intro``/``settling``) are a fresh account's quietest, the join cap is lowest there,
and an account that searches and joins on day one is the classic farm pattern. The
phase is the safer of the age phase and the persisted one, like the join cap's. Only in
a normal or binge visit (a glance or a reply-only visit is too short to wander off), with
``self_search_probability``, at most ``self_search_daily_max`` times in 24 hours, and
not after ``self_search_fruitless_max`` fruitless searches in a week.

**Its cost.** Every read books one action on the visit's budget immediately before it
goes out, exactly like a channel read, and the search is planned so that
``self_search_reserve_actions`` actions stay for the channels after it — it can never
push the account past its daily budget. The joins of its finds ride the channel walk, so
they obey the per-phase join cap like any other join.

**A rate limit stops the visit.** A flood or peer flood on a search read is folded into
the cycle's tally the way a join's is, nothing else is read, and the cycle ends: the loop
parks the account on the deadline Telegram gave, as for every other warming action.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING

from core.config import settings
from core.logging import log_event
from core.phone_geo import country_for_phone
from core.repositories.warming_character import fetch_account_character
from core.repositories.warming_pool import add_found_warming_channels, list_known_warming_channels
from core.repositories.warming_searches import list_channel_searches_since, record_channel_search
from schemas._warming_pool import ChannelSearchRecord
from services.warming import _seams
from services.warming._fleet import _affinity_floor
from services.warming._phases import _PHASE_ORDER
from services.warming._pool_hunt import HuntPlan, hunt_channels
from services.warming._pool_topics import everyday_topics
from services.warming._snapshot import current_snapshot
from services.warming._steps import _human_pause

if TYPE_CHECKING:
    from pydantic import BaseModel

    from schemas.accounts import AccountRead
    from schemas.telegram_actions import TelegramReadAction
    from schemas.warming import WarmingChannel, WarmingPhase
    from services.warming._pool_hunt import HuntOutcome, Reader
    from services.warming._steps import _ChannelTally

_FULL_VISITS = frozenset({"normal", "binge"})
_SIMILAR_SEEDS = 1
# One read per probe for the full channel, one for its last post.
_READS_PER_PROBE = 2
# The smallest search worth making: one query and one probe.
_MIN_READS = 1 + _READS_PER_PROBE
_DAY = timedelta(days=1)
_WEEK = timedelta(days=7)
# At most this many of its finds lead the visit's channel walk.
_WALK_FIRST = 2


@dataclass(slots=True)
class SelfSearchVisit:
    """What one visit tells the search: who, in which phase, with how much budget."""

    account: AccountRead
    language: str | None
    phase: WarmingPhase
    visit_kind: str
    channels: list[WarmingChannel]
    tally: _ChannelTally
    remaining_actions: int | None
    found: list[WarmingChannel] = field(default_factory=list)


def _safe_phase(account_id: str, age_phase: WarmingPhase) -> WarmingPhase:
    """The safer of the cycle's age phase and the phase the loop last persisted."""
    snapshot = current_snapshot(account_id)
    record = snapshot.record if snapshot is not None else None
    if record is None or record.current_phase is None:
        return age_phase
    return min(age_phase, record.current_phase, key=_PHASE_ORDER.index)


def _needs_channels(visit: SelfSearchVisit, language: str) -> bool:
    """Its language is short in the pool, or it has too few channels of its own finding."""
    own_language = [c for c in visit.channels if c.language == language]
    found_here = [c for c in visit.channels if c.found_by == visit.account.account_id]
    return (
        len(own_language) < _affinity_floor()
        or len(found_here) < settings.warming.self_search_target_found
    )


def _reads_left(visit: SelfSearchVisit) -> int | None:
    """Reads the search may book and still leave the reserve; ``None`` = no cap."""
    if visit.remaining_actions is None:
        return None
    left = visit.remaining_actions - visit.tally.attempts
    return left - settings.warming.self_search_reserve_actions


def _worth_a_look(visit: SelfSearchVisit) -> bool:
    """The free gates, in the order that keeps a refused visit at zero queries."""
    warm = settings.warming
    if not warm.self_search_enabled or warm.self_search_daily_max <= 0:
        return False
    if visit.visit_kind not in _FULL_VISITS or visit.language is None:
        return False
    phase = _safe_phase(visit.account.account_id, visit.phase)
    if _PHASE_ORDER.index(phase) < _PHASE_ORDER.index(warm.self_search_min_phase):
        return False
    reads = _reads_left(visit)
    if reads is not None and reads < _MIN_READS:
        return False
    if not _needs_channels(visit, visit.language):
        return False
    return _seams.rng.random() < warm.self_search_probability


async def _due(account_id: str, now: datetime) -> bool:
    """Under the daily cap, and not stuck finding nothing all week."""
    warm = settings.warming
    rows = [
        row
        for row in await list_channel_searches_since(now - _WEEK, account_id=account_id)
        if row.origin == "self"
    ]
    today = [row for row in rows if datetime.fromisoformat(row.created_at) >= now - _DAY]
    fruitless = [row for row in rows if row.status == "ok" and row.added == 0]
    return (
        len(today) < warm.self_search_daily_max and len(fruitless) < warm.self_search_fruitless_max
    )


def _reader(visit: SelfSearchVisit) -> Reader:
    """Book each read on the visit's tally right before it goes out, after a person's pause."""
    warm = settings.warming
    account_id = visit.account.account_id

    async def read(action: TelegramReadAction) -> BaseModel:
        await _human_pause(warm.action_delay_min_seconds, warm.action_delay_max_seconds)
        visit.tally.attempts += 1
        return await _seams.execute_owned_read(account_id, action)

    return read


def _fold_flood(visit: SelfSearchVisit, outcome: HuntOutcome) -> None:
    """A search read's rate limit, onto the cycle's tally as a join's would be."""
    tally = visit.tally
    if outcome.failures:
        tally.failures += outcome.failures
        tally.last_failed_action = "self_search"
    flood = outcome.flood
    if flood is None:
        return
    tally.last_failed_action = "self_search"
    if flood.seconds is None:
        tally.peer_flooded = True
        return
    tally.flooded = True
    tally.flood_seconds = flood.seconds
    tally.flood_until = (datetime.now(UTC) + timedelta(seconds=flood.seconds)).isoformat()


async def _plan(visit: SelfSearchVisit, language: str) -> HuntPlan:
    warm = settings.warming
    account_id = visit.account.account_id
    character = await fetch_account_character(account_id)
    interests = list(character.interests) if character is not None else []
    interests = interests or everyday_topics(language)
    queries = _seams.rng.sample(interests, min(warm.self_search_queries, len(interests)))
    planned = len(queries) + _SIMILAR_SEEDS + _READS_PER_PROBE * warm.self_search_max_probes
    reads = _reads_left(visit)
    return HuntPlan(
        language=language,
        country=(character.country if character else None)
        or country_for_phone(visit.account.phone),
        topics=tuple(queries),
        source="self_search",
        max_probes=warm.self_search_max_probes,
        max_reads=planned if reads is None else min(planned, reads),
        similar_seeds=_SIMILAR_SEEDS,
        found_by=account_id,
    )


async def search_for_self(visit: SelfSearchVisit) -> bool:
    """Maybe search, filling ``visit.found`` with what to walk first; ``True`` = stop the visit.

    ``True`` only when Telegram rate-limited a search read: the tally then carries the
    flood exactly as a join's would, and the cycle must not send anything more.
    """
    if not _worth_a_look(visit) or visit.language is None:
        return False
    now = datetime.now(UTC)
    account_id = visit.account.account_id
    if not await _due(account_id, now):
        return False
    plan = await _plan(visit, visit.language)
    if not plan.topics:
        return False
    outcome = await hunt_channels(_reader(visit), plan, await list_known_warming_channels())
    _fold_flood(visit, outcome)
    added = set(await _keep(visit, outcome, now))
    new = [c for c in outcome.fits if c.channel in added]
    walkable = {c.channel: c for c in visit.channels if c.language in {visit.language, None}}
    seen = [walkable[name] for name in outcome.known_hits if name in walkable]
    visit.found = (new + seen)[:_WALK_FIRST]
    return outcome.flood is not None


async def _keep(visit: SelfSearchVisit, outcome: HuntOutcome, now: datetime) -> list[str]:
    """Add the fits to the pool, remember the search, log it; the channels that were new."""
    account_id = visit.account.account_id
    added = await add_found_warming_channels(outcome.fits, settings.warming.max_channels_total)
    await record_channel_search(
        ChannelSearchRecord(
            origin="self",
            account_id=account_id,
            language=visit.language or "",
            status="flood" if outcome.flood is not None else "ok",
            found=outcome.found,
            added=len(added),
            created_at=now.isoformat(),
        )
    )
    await log_event(
        "WARNING" if outcome.flood is not None else "INFO",
        "warming_self_search_done",
        account_id=account_id,
        extra={"found": outcome.found, "checked": outcome.checked, "added": len(added)},
    )
    return added


def walk_found_first(
    chosen: list[WarmingChannel], found: list[WarmingChannel]
) -> list[WarmingChannel]:
    """The visit's channels with the account's fresh finds leading, the count kept.

    The finds replace the last picks rather than adding to them, so the visit is no
    longer than it would have been — unless it found more than it meant to walk.
    """
    if not found:
        return chosen
    names = {c.channel for c in found}
    rest = [c for c in chosen if c.channel not in names]
    return [*found, *rest][: max(len(chosen), len(found))]
