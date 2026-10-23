"""A phone visit's plan: what the session in the app may do, and for how long.

Split from ``_mobile_cycle``, which opens, runs and parks the pod around it. The plan
follows the operator's switches as the Telethon cycle does: reactions, story viewing,
and the extras toggles that cover what the app does too (the message search for its
search, video and voice for its notes; no switch covers a photo, so it stays).
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from core.config import settings
from core.db import load_warming_settings
from core.repositories.accounts import fetch_account
from schemas.emulator import PlaybookPlan
from services._account_language import account_language
from services.warming import _seams
from services.warming._phases import persona_reaction_probability
from services.warming._snapshot import current_snapshot

if TYPE_CHECKING:
    from schemas.warming import WarmingCycleRequest

# One budgeted action buys this many seconds in the app; a cycle gets at least
# the floor, and never more than the emulator's session cap.
_SECONDS_PER_ACTION = 30
_MIN_SECONDS = 60
# The chance a visit searches the app for a channel, by persona; a glance never does,
# nor looks at photos and notes.
_SEARCH_CHANCE = {"calm": 0.15, "normal": 0.3, "active": 0.5}


def session_seconds(budget: int | None) -> int:
    """The seconds a visit with ``budget`` actions spends in the app."""
    cap = settings.emulator.activity_max_seconds
    if budget is None:
        return cap
    return min(cap, max(_MIN_SECONDS, budget * _SECONDS_PER_ACTION))


async def session_plan(request: WarmingCycleRequest) -> PlaybookPlan:
    """The visit's shape for the app, as the Telethon cycle would walk it.

    Its channels and stories order, the persona's reaction chance (none while the
    operator turned reactions off), stories only while story viewing is on and never
    on a glance, and the visit's action budget as the session's cap. Beyond a glance
    it looks at photos, plays notes while the video or voice extra is on, and with
    the persona's ``_SEARCH_CHANCE`` searches, while the message search extra is on,
    for one of the phone language's words. It never joins, makes folders or opens a
    Mini App: those change the account and stay off until the operator asks for them.
    """
    snapshot = current_snapshot(request.account_id)
    controls = snapshot.controls if snapshot else await load_warming_settings()
    toggles = controls.extra_toggles
    visit = request.visit
    reaction = persona_reaction_probability(request.activity_persona)
    glance = visit is not None and visit.kind == "glance"
    searching = (
        not glance
        and toggles.get("search_messages", False)
        and _seams.rng.random() < _SEARCH_CHANCE[request.activity_persona]
    )
    return PlaybookPlan(
        channels=visit.channels if visit else None,
        reaction_probability=reaction if controls.reactions_enabled else 0.0,
        stories=settings.warming.story_view_enabled and not glance,
        stories_first=visit.stories_first if visit else False,
        max_actions=request.remaining_actions,
        media=not glance,
        notes=toggles.get("video", False) or toggles.get("voice", False),
        search_queries=[_seams.rng.choice(await _search_words(request.account_id))]
        if searching
        else [],
    )


async def _search_words(account_id: str) -> list[str]:
    """The words of the phone's language, or English where none can be typed."""
    snapshot = current_snapshot(account_id)
    account = snapshot.account if snapshot else await fetch_account(account_id)
    language = account_language(account.phone) if account is not None else None
    by_language = settings.emulator.search_queries_by_language
    return by_language.get(language or "") or settings.emulator.search_queries
