"""Discovery stage 1 — what every wave shares: the budget, the wave result, the pacing.

Split from ``_discovery_waves`` (file-size cap). Nothing here talks to Telegram.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, NamedTuple

from core.config import settings
from services.neurocomment import _seams
from services.neurocomment._discovery_providers import SourceOutcome

if TYPE_CHECKING:
    from schemas.neurocomment_discovery import DiscoverySource
    from services.neurocomment._discovery_pool import AccountPool

# Short locale-neutral reason for a wave the run's read budget stopped. Deliberately not
# a run-level error — see ``_discovery_search._merge``.
READ_BUDGET = "read_budget"
# A recommendation wave not asked because recommendations only ever return channels.
KIND_UNSUPPORTED = "kind_unsupported"

# Native hits win a cross-source tie: their handles come straight from Telegram in
# canonical case, which is what adopt writes into the campaign verbatim. This governs
# the DEDUP order only — see ``_discovery_search._merge`` for why it must not govern
# truncation as well.
# Annotated because the keys are also iterated as the report's source list, where a
# widened ``str`` would not satisfy ``DiscoverySourceReport``.
# Also the board's row order — which the run's wave order no longer follows, since the
# operator's seed is read ahead of the wider post wave.
SOURCE_PRIORITY: dict[DiscoverySource, int] = {
    "telegram_search": 0,
    "telegram_posts": 1,
    "telegram_similar": 2,
    "telegram_recommended": 3,
}


class Wave(NamedTuple):
    """One wave's outcomes, and why the run must stop reading after it, if it must.

    ``stop`` is the reason the pool has no account left: ``flooded`` (a read of ours
    landed a limit and wrote the cooldown), ``cooling`` (a limit somebody else recorded
    was found before a read), or ``aborted`` (a dead session, nothing written). Only the
    first two keep the run from replacing its stored candidates.
    """

    outcomes: list[SourceOutcome]
    stop: str | None = None

    @property
    def stopped(self) -> bool:
        return self.stop is not None


def stopped(outcomes: list[SourceOutcome], pool: AccountPool, source: DiscoverySource) -> Wave:
    """A wave the pool would not hand an account to.

    Every account gone is a stop, and at this point only ``cooling`` can have emptied it
    — a flood or a dead session is reported by ``report`` and ends the wave before the
    next acquire. Accounts still in the pool means each has spent its own wave ceiling:
    truncation, reported like the shared budget's, and the run goes on to qualify.
    """
    if pool.empty:
        return Wave(outcomes, stop="cooling")
    return Wave([*outcomes, skipped(source, READ_BUDGET, truncated=True)])


class Budget:
    """Telegram reads the run has left, shared by every wave.

    One counter, not a cap per wave: what has to stay bounded is the traffic this run
    emits per account, and spending it in wave order is what puts the cheap keyword
    sweep first in line for it.

    ``held`` fences reads off for a wave that has not run yet. Wave order alone left the
    LAST wave — Telegram's recommendations, the only source that reaches channels the
    account is nowhere near — living on whatever the post pages had not eaten, which on a
    full keyword list was nothing at all.
    """

    def __init__(self, total: int) -> None:
        self.left = total
        self.held = 0

    def take(self) -> bool:
        """Claim one read. ``False`` means the run is out and the wave must stop."""
        if self.left - self.held <= 0:
            return False
        self.left -= 1
        return True


def skipped(
    source: DiscoverySource,
    reason: str | None = None,
    *,
    truncated: bool = False,
) -> SourceOutcome:
    """A source that was not asked (again) — never a silent absence from the report."""
    return SourceOutcome(source=source, state="skipped", error=reason, truncated=truncated)


async def pace() -> None:
    """The jittered gap between two real Telegram reads, waves and probes alike."""
    neuro = settings.neurocomment
    await _seams.sleep(
        _seams.rng.uniform(
            neuro.discovery_qualify_delay_min_seconds,
            neuro.discovery_qualify_delay_max_seconds,
        ),
    )


def unreached(outcomes: list[SourceOutcome]) -> list[SourceOutcome]:
    """Name every source this run never got to, so none is silently absent from the strip.

    A flood, or a sweep with no hit to seed the recommendation wave from, used to drop a
    whole row off the board — leaving the operator to guess whether that source had
    answered with nothing or had never been asked. No reason code: a FloodWait already
    rides the run's error and a second one would only compete with it.
    """
    reached = {outcome.source for outcome in outcomes}
    return [skipped(source) for source in SOURCE_PRIORITY if source not in reached]
