"""One account's warming history — every wave of its current life, read from the journal.

The waves are the survival counter's own (``_stints``), so the card's history and the
swimmer row can never disagree about how many waves an account had. Only the current
life is shown: a re-imported account does not inherit a deleted one's waves.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from core.repositories.accounts import fetch_account
from core.repositories.lifecycle import list_lifecycle_events
from core.repositories.warming import fetch_warming_state
from schemas._warming_waves import WarmingWave, WarmingWaveList
from services.lifecycle_journal import current_life
from services.survival._stints import build_life, parse_moment

if TYPE_CHECKING:
    from schemas._warming_waves import WaveEndReason
    from schemas.lifecycle import LifecycleEvent
    from schemas.warming import WarmingStateRecord
    from services.survival._stints import Stint

_RELEASES = frozenset({"warmed", "promoted"})
_STOP_REASONS: dict[object, WaveEndReason] = {
    "operator": "stopped",
    "spam_blocked": "spam_blocked",
}


def _int(value: object) -> int | None:
    return value if isinstance(value, int) and not isinstance(value, bool) else None


def _end(event: LifecycleEvent) -> tuple[WaveEndReason, int | None] | None:
    """How ``event`` ends a wave and the worked days it names, or ``None``."""
    if event.kind in _RELEASES:
        days = _int(event.extra.get("warming_days"))
        return "released", days if days is not None else _int(event.extra.get("worked_days"))
    if event.kind == "warming_stopped":
        reason = _STOP_REASONS.get(event.extra.get("reason"), "stopped")
        return reason, _int(event.extra.get("worked_days"))
    if event.kind == "session_dead":
        return "session_dead", None
    if event.kind == "deleted":
        return "deleted", None
    return None


def _ended_at(event: LifecycleEvent) -> str:
    """A death ends its wave when it was first shown, not when a check found it."""
    raw = event.extra.get("evidence_at")
    if event.kind != "session_dead" or not isinstance(raw, str):
        return event.created_at
    evidence, found = parse_moment(raw), parse_moment(event.created_at)
    if evidence is None or found is None or evidence >= found:
        return event.created_at
    return raw


def _wave(wave: Stint, following: Stint | None, record: WarmingStateRecord | None) -> WarmingWave:
    opening = wave.events[0].extra if wave.events[0].kind == "warming_started" else {}
    own = record if record is not None and record.started_at == wave.start_key else None
    ended_at: str | None = None
    reason: WaveEndReason | None = None
    worked: int | None = None
    for event in wave.events:
        found = _end(event)
        if found is not None:
            ended_at, (reason, worked) = _ended_at(event), found
            break
    if reason is None and following is not None:
        ended_at, reason = following.start_key, "replaced"
    if worked is None and own is not None:
        worked = own.warmed_days
    persona = opening.get("persona")
    return WarmingWave(
        wave=wave.wave,
        started_at=wave.start_key,
        ended_at=ended_at,
        end_reason=reason,
        worked_days=worked,
        target_days=_int(opening.get("target_days")) or (own.target_days if own else None),
        persona=persona if isinstance(persona, str) else (own.activity_persona if own else None),
    )


def account_waves(
    events: list[LifecycleEvent],
    record: WarmingStateRecord | None,
) -> list[WarmingWave]:
    """The waves of one life's rows (journal order), with the live row for the last one."""
    life = build_life(events) if events else None
    if life is None:
        return []
    waves = life.waves
    return [
        _wave(wave, waves[index + 1] if index + 1 < len(waves) else None, record)
        for index, wave in enumerate(waves)
    ]


async def load_account_waves(account_id: str) -> WarmingWaveList | None:
    """Every wave of the account's current life, oldest first; ``None`` if no such account."""
    account = await fetch_account(account_id)
    if account is None:
        return None
    events = current_life(await list_lifecycle_events(account_id), account.created_at)
    record = await fetch_warming_state(account_id)
    return WarmingWaveList(account_id=account_id, waves=account_waves(events, record))
