"""What each kind of evidence is worth: the policy table of design section 5.3, in code.

Weights, half-lives and default expiries are policy, not data: a row stores what was observed
and when, and the fold reads this table. Changing a number here re-folds history on the next
write with no migration. Pure: no clock, no database.

``strength(t) = base * 0.5 ** ((t - observed_at) / half_life)``, and 0 from ``expires_at`` on.
A kind with no half-life does not age. A SHADOW kind (a success that proves nothing yet) has
base 0: it is recorded so its agreement with @SpamBot can be measured, and moves no verdict.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import timedelta
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from datetime import datetime

    from schemas.spam_evidence import Evidence, EvidenceDirection, EvidenceKind

_HOUR = 3600.0


@dataclass(frozen=True)
class KindPolicy:
    """One row of the table: direction, base weight, half-life, default time to live."""

    direction: EvidenceDirection
    base: float
    half_life_hours: float | None = None
    # Applied by ``record_evidence`` when the caller gives no ``expires_at``.
    ttl_hours: float | None = None


KIND_POLICY: dict[EvidenceKind, KindPolicy] = {
    "spambot_limited": KindPolicy("limited", 1.0),
    "spambot_clean": KindPolicy("clean", 1.0, half_life_hours=120.0),
    "spambot_unreadable": KindPolicy("none", 0.0),
    "spambot_reviewing": KindPolicy("none", 0.0, ttl_hours=6.0),
    "peer_flood_send": KindPolicy("limited", 0.7, half_life_hours=72.0),
    "peer_flood_other": KindPolicy("limited", 0.4, half_life_hours=48.0),
    "user_banned_in_channel": KindPolicy("limited", 0.5, half_life_hours=72.0),
    # A platform restriction is a flag beside the verdict, never a spam limit.
    "tos_restricted": KindPolicy("none", 0.0),
    "tos_cleared": KindPolicy("none", 0.0),
    "freeze_seen": KindPolicy("frozen", 1.0),
    # Closes ``freeze_seen``; it is not clean evidence (direction ``none``).
    "freeze_cleared": KindPolicy("none", 1.0),
    # The one documented proof: a first message delivered to a stranger.
    "dm_stranger_ok": KindPolicy("clean", 0.8, half_life_hours=48.0),
    # Shadow kinds: weight 0 until measured against @SpamBot.
    "group_send_ok": KindPolicy("clean", 0.0),
    "join_ok": KindPolicy("clean", 0.0),
    "read_ok": KindPolicy("clean", 0.0),
    "dm_known_peer_ok": KindPolicy("clean", 0.0),
    "flood_wait": KindPolicy("none", 0.0),
}

SPAMBOT_KINDS: frozenset[EvidenceKind] = frozenset(
    {"spambot_limited", "spambot_clean", "spambot_unreadable", "spambot_reviewing"}
)


def strength(item: Evidence, now: datetime) -> float:
    """What the item is worth at ``now``: decayed from its base, and 0 once it has expired."""
    if item.expires_at is not None and now >= item.expires_at:
        return 0.0
    policy = KIND_POLICY[item.kind]
    if policy.half_life_hours is None:
        return policy.base
    # An item stamped in the future (clock skew) is as fresh as it can be, never stronger.
    age_hours = max((now - item.observed_at).total_seconds(), 0.0) / _HOUR
    return policy.base * 0.5 ** (age_hours / policy.half_life_hours)


def default_expiry(kind: EvidenceKind, observed_at: datetime) -> datetime | None:
    """The kind's own time to live from ``observed_at``, if it has one."""
    ttl = KIND_POLICY[kind].ttl_hours
    return None if ttl is None else observed_at + timedelta(hours=ttl)
