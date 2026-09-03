"""Discovery stage 1 — what every wave shares: the budget, the wave result, the pacing.

The pacing gap is per STREAM now: ``services.neurocomment._discovery_streams.Streams``
calls ``pace()`` between two reads of the same account, never between two accounts.
Split from ``_discovery_waves`` (file-size cap). Nothing here talks to Telegram.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, NamedTuple

from core.config import settings
from services.neurocomment import _seams
from services.neurocomment._discovery_providers import SourceOutcome

if TYPE_CHECKING:
    from schemas.neurocomment_discovery import DiscoverySource

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

    def exhausted_for(self, *, held: bool) -> bool:
        """Is the run out for a job with this ``held`` flag?

        Checked BEFORE the pace sleep and the account check, consumed after — claiming
        the read up front charged one for every check a stream then found refused, a
        phantom read whenever every account sat at its ceiling. A ``held`` job draws
        only from the fenced-off share (``left``); a normal job must also leave that
        share untouched for the wave it is reserved for.
        """
        if held:
            return self.left <= 0
        return self.left - self.held <= 0

    @property
    def exhausted(self) -> bool:
        """Kept for callers with no held reads of their own: ``exhausted_for(held=False)``."""
        return self.exhausted_for(held=False)

    def take(self, *, held: bool = False) -> None:
        """Consume the read an account was just handed out for."""
        self.left -= 1
        if held:
            self.held -= 1


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
