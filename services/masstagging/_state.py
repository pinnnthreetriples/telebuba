"""In-memory run state: generations, start/edit claims, the live pace, the media cursor.

The shape of ``services.chat_broadcast._state``, for the same reasons: one process, one
event loop, so a function with no ``await`` in it is atomic, and nothing here is worth
persisting — a restart is a full repair from the campaign rows.

* A run GENERATION only ever rises; Pause bumps it and every external call checks it.
* ``_RUN_OWNER`` is the run entitled to write the terminal row.
* Start and settings saves exclude each other through counted claims.
* «Темп» saved from the board while a run works: the run reads it instead of its copy.
* Which story media is next, so the pool is taken in turn.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from schemas.masstagging import MasstaggingPace

_RUN_GENERATIONS: dict[str, int] = {}
_RUN_OWNER: dict[str, str] = {}
_STARTING: set[str] = set()
_EDITING: dict[str, int] = {}
_PACE: dict[str, MasstaggingPace] = {}
_MEDIA_CURSOR: dict[str, int] = {}
_NEXT_STEP: dict[str, dict[str, int]] = {}


def begin_run(campaign_id: str, run_id: str) -> int:
    generation = _RUN_GENERATIONS[campaign_id] = _RUN_GENERATIONS.get(campaign_id, 0) + 1
    _RUN_OWNER[campaign_id] = run_id
    return generation


def run_is_current(campaign_id: str, generation: int) -> bool:
    return _RUN_GENERATIONS.get(campaign_id) == generation


def abandon_run(campaign_id: str, run_id: str) -> None:
    """Forget a run that never reached the database or spawned a task."""
    if _RUN_OWNER.get(campaign_id) == run_id:
        _RUN_OWNER.pop(campaign_id, None)
        revoke_run(campaign_id)


def revoke_run(campaign_id: str) -> None:
    _RUN_GENERATIONS[campaign_id] = _RUN_GENERATIONS.get(campaign_id, 0) + 1


def revoke_run_if_current(campaign_id: str, run_id: str | None) -> bool:
    """Fence the run ``run_id`` names; ``False`` when a newer run owns the campaign."""
    owner = _RUN_OWNER.get(campaign_id)
    if run_id is not None and owner is not None and owner != run_id:
        return False
    revoke_run(campaign_id)
    return True


def claim_settlement(campaign_id: str, run_id: str) -> bool:
    """May ``run_id`` write the terminal row? Refused only to a superseded run."""
    owner = _RUN_OWNER.get(campaign_id)
    if owner is not None and owner != run_id:
        return False
    _RUN_OWNER.pop(campaign_id, None)
    return True


def try_claim_start(campaign_id: str) -> bool:
    if campaign_id in _STARTING or campaign_id in _EDITING:
        return False
    _STARTING.add(campaign_id)
    return True


def finish_start(campaign_id: str) -> None:
    _STARTING.discard(campaign_id)


def start_in_flight(campaign_id: str) -> bool:
    return campaign_id in _STARTING


def try_claim_edit(campaign_id: str) -> bool:
    if campaign_id in _STARTING:
        return False
    _EDITING[campaign_id] = _EDITING.get(campaign_id, 0) + 1
    return True


def finish_edit(campaign_id: str) -> None:
    remaining = _EDITING[campaign_id] - 1
    if remaining:
        _EDITING[campaign_id] = remaining
    else:
        del _EDITING[campaign_id]


def set_pace(campaign_id: str, pace: MasstaggingPace) -> None:
    _PACE[campaign_id] = pace


def live_pace(campaign_id: str) -> MasstaggingPace | None:
    """«Темп» saved since the run read its settings; ``None`` = the run's own copy holds."""
    return _PACE.get(campaign_id)


def forget_pace(campaign_id: str) -> None:
    """A run about to read its settings from the row needs no override on top."""
    _PACE.pop(campaign_id, None)


def next_media(campaign_id: str, count: int) -> int:
    """The index of the story media to use next, taking the pool in turn."""
    index = _MEDIA_CURSOR.get(campaign_id, 0) % count
    _MEDIA_CURSOR[campaign_id] = index + 1
    return index


def note_next_step(campaign_id: str, account_id: str, at_unix: int) -> None:
    """When this account's next story or dialog is due, for the pipeline's line."""
    _NEXT_STEP.setdefault(campaign_id, {})[account_id] = at_unix


def next_step(campaign_id: str) -> int | None:
    """The soonest next step of the campaign's accounts, if a run is pausing between."""
    steps = _NEXT_STEP.get(campaign_id)
    return min(steps.values()) if steps else None


def forget_campaign(campaign_id: str) -> None:
    """A run settled or a campaign deleted: its live pace, media cursor and steps go too."""
    _PACE.pop(campaign_id, None)
    _MEDIA_CURSOR.pop(campaign_id, None)
    _NEXT_STEP.pop(campaign_id, None)


def reset_for_tests() -> None:
    _RUN_GENERATIONS.clear()
    _RUN_OWNER.clear()
    _STARTING.clear()
    _EDITING.clear()
    _PACE.clear()
    _MEDIA_CURSOR.clear()
    _NEXT_STEP.clear()
