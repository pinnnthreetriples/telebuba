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


# Memoised affinity subsets. The result is a pure function of the account, its
# language, the epoch, the channel names and labels, and the (runtime-static)
# selection config, so a hit skips re-hashing two SHA-256 per channel + a full
# re-sort every cycle. The config fields are folded into the key so a test varying
# them mid-run recomputes rather than reading a stale slice; in production they
# never change. Prior-epoch entries are evicted on miss (see below) so the dict
# stays bounded to the live epoch rather than growing forever. Cleared per test by
# the warming conftest.
_AFFINITY_CACHE: dict[tuple[object, ...], list[WarmingChannel]] = {}


def _affinity_floor() -> int:
    warm = settings.warming
    return max(warm.channel_affinity_min, warm.channels_per_cycle_min)


def _language_pool(
    channels: list[WarmingChannel], language: str | None
) -> tuple[list[WarmingChannel], dict[str, int]]:
    """The channels an account of ``language`` may draw from, each with its tier.

    Tier 0 is the account's own language, tier 1 the unlabelled channels, tier 2 the
    rest; a later tier joins only while the pool is still under the floor, so a
    Mexican number reads Spanish channels and touches others only when there are too
    few. An unknown account language keeps the whole pool in one tier.
    """
    if language is None:
        return channels, dict.fromkeys((c.channel for c in channels), 0)
    tiers = (
        [c for c in channels if c.language == language],
        [c for c in channels if c.language is None],
        [c for c in channels if c.language not in {language, None}],
    )
    floor = _affinity_floor()
    pool: list[WarmingChannel] = []
    rank: dict[str, int] = {}
    for tier, members in enumerate(tiers):
        if len(pool) >= floor:
            break
        pool += members
        rank.update(dict.fromkeys((c.channel for c in members), tier))
    return pool, rank


def _account_channel_affinity(
    account_id: str,
    channels: list[WarmingChannel],
    epoch: int = 0,
    language: str | None = None,
) -> list[WarmingChannel]:
    """A stable per-account interest subset of the global channel pool.

    The pool is shared by every account, so sampling each cycle straight from it
    makes N accounts overlap almost entirely on which channels they join/read/
    react to — a correlated-subscription-graph tell. Humans instead follow a
    fixed set of interests, so we carve a deterministic per-account slice (ranked
    by a salted, process-stable :func:`_stable_fraction`) and let each cycle
    sample from *that*. A small ``epoch``-keyed drift is added to each channel's
    score so membership churns slowly over time (``churn_strength`` keeps it to
    the channels near the cutoff — gradual, not a wholesale swap). The slice is
    carved from the account's :func:`_language_pool`, own-language channels
    first, and is never smaller than the affinity floor; pools no larger than
    that floor stay whole (nothing to subdivide). Channels the account found by its
    own search (``found_by``) rank ahead of everything else, so they never churn out of
    its interests: it went looking for them.
    """
    warm = settings.warming
    key = (
        account_id,
        epoch,
        language,
        frozenset((c.channel, c.language, c.found_by == account_id) for c in channels),
        _affinity_floor(),
        warm.channel_affinity_ratio,
        warm.channel_affinity_churn_strength,
        warm.fleet_hash_salt,
    )
    cached = _AFFINITY_CACHE.get(key)
    if cached is not None:
        return cached

    # A miss for the current epoch means any entry from an older epoch is dead
    # (epoch is shared fleet-wide), so evict them to keep the cache bounded.
    for stale in [k for k in _AFFINITY_CACHE if k[1] != epoch]:
        del _AFFINITY_CACHE[stale]

    pool, rank = _language_pool(channels, language)
    for channel in pool:
        if channel.found_by == account_id:
            rank[channel.channel] = -1
    floor = _affinity_floor()
    if len(pool) <= floor:
        result = pool
    else:
        k = min(len(pool), max(floor, round(len(pool) * warm.channel_affinity_ratio)))
        strength = warm.channel_affinity_churn_strength

        def _score(channel: WarmingChannel) -> tuple[int, float]:
            base = _stable_fraction(f"aff:{account_id}:{channel.channel}")
            drift = _stable_fraction(f"aff:{account_id}:{channel.channel}:{epoch}")
            return rank[channel.channel], base + strength * drift

        result = sorted(pool, key=_score)[:k]

    _AFFINITY_CACHE[key] = result
    return result


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
    probability; a ``0`` probability for the day's class disables it. ``day_iso``
    is the account's local calendar day (matching ``daily_count_date``), so the
    weekend is the account's own weekend.
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
