"""Which account the survival probe asks @SpamBot about next. Pure: the clock is passed in.

An account is due when its current life was released, it is neither dead nor written
off, a post-release window (day 7, day 30) has closed, and no @SpamBot answer — of any
kind — has come since that window's end, less one cache lifetime (the same ``grace`` the
outcome rule uses). Only the latest closed window counts, so an account asks twice over
its whole life at most, and an old release asks once. A probe that got no answer is not
retried before ``retry``, and an account is skipped while another account behind the
same proxy was asked within ``spacing``. The most overdue account goes first.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

from schemas.survival import SURVIVAL_WINDOW_DAYS
from services.survival._outcomes import window_end

if TYPE_CHECKING:
    from datetime import datetime, timedelta

    from schemas.survival import SurvivalWindow
    from services.survival._outcomes import LifeFacts

# Accounts without a proxy all share the host's address.
DIRECT = "direct"


@dataclass(frozen=True)
class ProbeCandidate:
    """A released, living account's current life and the proxy it speaks through."""

    account_id: str
    proxy_key: str
    facts: LifeFacts


@dataclass(frozen=True)
class ProbePacing:
    """The clock and the pacing rules of one pass."""

    now: datetime
    grace: timedelta
    spacing: timedelta
    retry: timedelta


@dataclass(frozen=True)
class DueProbe:
    """The account to ask and the window the answer is for."""

    account_id: str
    proxy_key: str
    window: SurvivalWindow
    window_end: datetime


def _closed_window(facts: LifeFacts, now: datetime) -> tuple[SurvivalWindow, datetime] | None:
    """The latest post-release window that has closed by ``now``."""
    closed: tuple[SurvivalWindow, datetime] | None = None
    for window in SURVIVAL_WINDOW_DAYS:
        end = window_end(facts, window)
        if end is not None and end <= now:
            closed = (window, end)
    return closed


def _due_window(
    candidate: ProbeCandidate,
    pacing: ProbePacing,
) -> tuple[SurvivalWindow, datetime] | None:
    facts = candidate.facts
    if facts.died_at is not None or facts.censored_at is not None:
        return None
    closed = _closed_window(facts, pacing.now)
    if closed is None:
        return None
    if any(moment >= closed[1] - pacing.grace for moment in facts.checks):
        return None
    return closed


def _recent(moment: datetime | None, now: datetime, span: timedelta) -> bool:
    return moment is not None and now - moment < span


def pick_due_probe(
    candidates: list[ProbeCandidate],
    asked_by_proxy: dict[str, datetime],
    attempts: dict[str, datetime],
    pacing: ProbePacing,
) -> DueProbe | None:
    """The single most overdue account a pass may ask about, or ``None``.

    ``asked_by_proxy`` is the latest @SpamBot ask behind each proxy (journal answers and
    this process's own attempts); ``attempts`` is this process's last probe per account.
    """
    due: list[DueProbe] = []
    for candidate in candidates:
        window = _due_window(candidate, pacing)
        if window is None or _recent(attempts.get(candidate.account_id), pacing.now, pacing.retry):
            continue
        if _recent(asked_by_proxy.get(candidate.proxy_key), pacing.now, pacing.spacing):
            continue
        due.append(DueProbe(candidate.account_id, candidate.proxy_key, window[0], window[1]))
    return min(due, key=lambda probe: probe.window_end, default=None)
