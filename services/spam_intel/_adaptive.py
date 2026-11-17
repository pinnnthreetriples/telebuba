"""The adaptive routine interval (owner decision D2, design 5.5 rule 5). Pure.

Warming asks @SpamBot every ``warming.spam_check_interval_hours`` (72 h). An account with a long
clean record and NOTHING limiting since is asked less often: ``routine_mid_hours`` (120 h) after
``routine_mid_after_clean`` clean readings in a row, ``routine_long_hours`` (168 h) after
``routine_long_after_clean``. The reason it is safe to ask less: a limit also shows up as
evidence (PeerFlood, a freeze) between two readings, and any such evidence, an unreadable answer
or a "being checked" answer ends the streak, so the next ask is back on the 72 h rhythm.
"""

from __future__ import annotations

from datetime import timedelta
from typing import TYPE_CHECKING

from services.spam_intel._evidence_policy import KIND_POLICY

if TYPE_CHECKING:
    from collections.abc import Iterable
    from datetime import datetime

    from core._config_spam_intel import SpamIntelSettings
    from schemas.spam_evidence import Evidence

# Evidence that ends a run of clean readings: it points at a limit or a freeze, or it is an
# @SpamBot answer that said nothing.
_BREAKS = frozenset({"limited", "frozen"})
_SILENT_ANSWERS = frozenset({"spambot_unreadable", "spambot_reviewing"})


def _breaks(item: Evidence) -> bool:
    policy = KIND_POLICY[item.kind]
    return item.kind in _SILENT_ANSWERS or (policy.direction in _BREAKS and policy.base > 0)


def clean_streak(evidence: Iterable[Evidence]) -> int:
    """How many @SpamBot ``clean`` readings in a row, newest first, nothing limiting between."""
    streak = 0
    for item in sorted(evidence, key=lambda e: e.observed_at, reverse=True):
        if _breaks(item):
            break
        if item.kind == "spambot_clean":
            streak += 1
    return streak


def last_clean_at(evidence: Iterable[Evidence]) -> datetime | None:
    """When the newest @SpamBot ``clean`` reading was taken."""
    stamps = [item.observed_at for item in evidence if item.kind == "spambot_clean"]
    return max(stamps, default=None)


def routine_interval(evidence: Iterable[Evidence], cfg: SpamIntelSettings) -> timedelta | None:
    """The stretched interval for this account, or ``None`` to keep warming's own."""
    streak = clean_streak(evidence)
    if streak >= cfg.routine_long_after_clean:
        return timedelta(hours=cfg.routine_long_hours)
    if streak >= cfg.routine_mid_after_clean:
        return timedelta(hours=cfg.routine_mid_hours)
    return None
