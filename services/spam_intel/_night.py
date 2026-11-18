"""D13: a ROUTINE probe waits out the account's local night (design 5.5, rule 13).

A routine check is scheduled, not urgent, and an account that talks to @SpamBot at 03:00 local
time is not behaving like its owner. So a ROUTINE ask made inside the account's local night is
deferred to that morning. Only the routine ask: a decision (a broadcast's chat-ban check, a
quarantine release), a measurement and the operator's click are never delayed by it.

The zone is the one warming already uses: the account's phone number (``core.phone_geo``). No
phone, or a number with no known zone, means no deferral: the rule never guesses a zone. The
window is whole local hours (``[start, end)``, default 00:00-07:00, wrapping midnight when start
is after end; start equal to end switches it off), and the morning is staggered by a stable
per-account offset of up to ``local_night_spread_minutes``, so a fleet whose night ends together
does not ask together. Nothing here reads the clock or the database.
"""

from __future__ import annotations

import zlib
from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING

from services._local_hours import in_hours, known_zone, last_hour_boundary, next_hour_boundary

if TYPE_CHECKING:
    from core._config_spam_intel import SpamIntelSettings

_OFFSET_STEPS = 10_000


def _morning_offset(account_id: str, cfg: SpamIntelSettings) -> timedelta:
    """This account's stable lag behind the end of the night, from 0 to the configured spread."""
    fraction = (zlib.crc32(account_id.encode()) % _OFFSET_STEPS) / _OFFSET_STEPS
    return timedelta(minutes=cfg.local_night_spread_minutes * fraction)


def night_until(
    moment: datetime, tz_name: str | None, account_id: str, cfg: SpamIntelSettings
) -> datetime | None:
    """When the account's local night (and its morning stagger) ends, if ``moment`` is inside it.

    ``None`` when the switch is off, the window is empty, the zone is unknown, or it is day.
    """
    start, end = cfg.local_night_start_hour, cfg.local_night_end_hour
    zone = known_zone(tz_name)
    if not cfg.local_night_defer or start == end or zone is None:
        return None
    local = moment.astimezone(zone)
    offset = _morning_offset(account_id, cfg)
    if in_hours(local, start, end):
        return (next_hour_boundary(local, end) + offset).astimezone(UTC)
    # The night is over, but this account wakes a little after it: still inside the stagger.
    woke = last_hour_boundary(local, end) + offset
    return woke.astimezone(UTC) if local < woke else None
