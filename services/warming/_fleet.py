"""Pure fleet de-correlation helpers — hashing, chronotype, channel affinity.

Deterministic, salted selection math shared by the pacing / cycle / loop modules
so a fleet of accounts driven off one shared config de-correlates — different
wake times, different channel-interest slices, different quiet days — instead of
moving in lockstep. No I/O; randomness enters as an argument, so every helper is
trivially testable and process-stable.
"""

from __future__ import annotations

import hashlib
from calendar import SATURDAY
from datetime import date
from typing import TYPE_CHECKING

from core.config import settings

if TYPE_CHECKING:
    from datetime import datetime
    from random import Random

    from schemas.warming import WarmingChannel


def _stable_fraction(key: str) -> float:
    """A deterministic, process-stable fraction in ``[0, 1)`` from a string key.

    Uses a SHA-256 digest (not the builtin ``hash``, which is per-process salted)
    so per-account / -channel / -day draws stay identical across restarts. The
    per-deployment ``fleet_hash_salt`` prefixes every key so two operators warming
    the same pool derive independent chronotypes / affinities / quiet-days.
    """
    salt = settings.warming.fleet_hash_salt
    digest = hashlib.sha256(f"{salt}:{key}".encode()).hexdigest()
    return int(digest[:8], 16) / 0x1_0000_0000


def _affinity_epoch(now: datetime) -> int:
    """The current slow-churn epoch index (advances every ``churn_days`` days).

    Folding the epoch into the affinity ranking lets the interest subset drift
    over weeks instead of being frozen for the account's lifetime.
    """
    return now.toordinal() // settings.warming.channel_affinity_churn_days


def _account_channel_affinity(
    account_id: str, channels: list[WarmingChannel], epoch: int = 0
) -> list[WarmingChannel]:
    """A stable per-account interest subset of the global channel pool.

    The pool is shared by every account, so sampling each cycle straight from it
    makes N accounts overlap almost entirely on which channels they join/read/
    react to — a correlated-subscription-graph tell. Humans instead follow a
    fixed set of interests, so we carve a deterministic per-account slice (ranked
    by a salted, process-stable :func:`_stable_fraction`) and let each cycle
    sample from *that*. A small ``epoch``-keyed drift is added to each channel's
    score so membership churns slowly over time (``churn_strength`` keeps it to
    the channels near the cutoff — gradual, not a wholesale swap). The subset is
    never smaller than ``channels_per_cycle_min`` so a cycle can always draw its
    floor; pools no larger than that floor stay whole (nothing to subdivide).
    """
    warm = settings.warming
    if len(channels) <= warm.channels_per_cycle_min:
        return channels
    k = min(
        len(channels),
        max(warm.channels_per_cycle_min, round(len(channels) * warm.channel_affinity_ratio)),
    )
    strength = warm.channel_affinity_churn_strength

    def _score(channel: WarmingChannel) -> float:
        base = _stable_fraction(f"aff:{account_id}:{channel.channel}")
        drift = _stable_fraction(f"aff:{account_id}:{channel.channel}:{epoch}")
        return base + strength * drift

    return sorted(channels, key=_score)[:k]


def _maybe_explore(
    chosen: list[WarmingChannel],
    channels: list[WarmingChannel],
    affinity: list[WarmingChannel],
    account_id: str,
    rng: Random,
) -> list[WarmingChannel]:
    """Occasionally swap one chosen channel for the account's top off-affinity one.

    A real user isn't a perfectly closed loop — now and then they open something
    just outside their core interests. With ``channel_exploration_probability`` we
    replace a single chosen channel (count unchanged) with the account's
    highest-ranked *off*-affinity channel. Picking by the same stable per-account
    score — not a uniform draw from the shared pool — keeps the explored channel
    de-correlated across the fleet: since a warming visit *joins* it permanently,
    a uniform draw would drift every account toward subscribing to the whole pool
    (membership overlap creeping back up, worst on the small pools this targets),
    whereas each account instead drifts toward *its own* secondary interest.
    """
    if not chosen or rng.random() >= settings.warming.channel_exploration_probability:
        return chosen
    affinity_names = {c.channel for c in affinity}
    off = [c for c in channels if c.channel not in affinity_names]
    if not off:
        return chosen
    pick = min(off, key=lambda c: _stable_fraction(f"aff:{account_id}:{c.channel}"))
    idx = rng.randrange(len(chosen))
    return [*chosen[:idx], pick, *chosen[idx + 1 :]]


def _is_quiet_day(account_id: str, day_iso: str) -> bool:
    """Whether the account rests for this whole calendar day (weekend-biased).

    Decided once per calendar day from a stable ``account:day`` hash — not per
    session: #202 removed the per-session version because independent rolls
    compounded into far too much idling. Weekends carry a higher rest
    probability; a ``0`` probability for the day's class disables it. The UTC
    calendar day (matching ``daily_count_date``) is a deliberate approximation —
    fine for a coarse weekly-seasonality signal.
    """
    warm = settings.warming
    try:
        weekday = date.fromisoformat(day_iso).weekday()
    except ValueError:
        return False
    prob = (
        warm.quiet_day_weekend_probability
        if weekday >= SATURDAY
        else warm.quiet_day_weekday_probability
    )
    if prob <= 0:
        return False
    return _stable_fraction(f"quiet:{account_id}:{day_iso}") < prob
