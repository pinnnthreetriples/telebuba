"""Shared vocabulary of the warming *extras* step — context, spec and the write dispatcher.

Split from :mod:`services.warming._extras` so the runner modules can import these
without importing the registry that imports them (one-way dependency, no cycle).

Budget model, in two tiers. A *read* extra never books the daily budget: it is what
every real client does for free on open, and the per-persona draw count is its
only cap. A *write* (or traffic) extra books ``tally.attempts`` immediately before
dispatch — the same book-before-dispatch invariant every other cycle step keeps
(#208): cancellation can land while the RPC is already outside the process, so its
outcome is ambiguous and must count spent.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Literal

from services.warming import _seams

if TYPE_CHECKING:
    from collections.abc import Awaitable, Callable

    from schemas._warming_extras import JoinedChannel
    from schemas.accounts import AccountRead
    from schemas.telegram_actions import ActionResult, TelegramAction
    from schemas.warming import ActivityPersona, WarmingChannel, WarmingSettingsSecret
    from services.warming._steps import _ChannelTally

    # ``None`` = not applicable this cycle: nothing was dispatched, nothing booked.
    _Runner = Callable[["_ExtraContext"], Awaitable[ActionResult | None]]

# The "look at posts just read" actions take at most this many ids (schema cap).
_POST_IDS_MAX = 5
# ``WarmConsumeMedia.max_bytes`` floor: below it a media extra cannot be built at all.
MEDIA_MIN_BYTES = 65_536

_ExtraKind = Literal["read", "write"]
# Cycle facts an extra cannot run without (``_extras._is_eligible``). Data, not
# lambdas, so the registry stays a table.
_Need = Literal["recent_ids", "joined", "premium", "media_bytes"]


@dataclass
class _ExtraContext:
    """Everything an extra may look at: the cycle's account, channels and running tally."""

    account_id: str
    secret: WarmingSettingsSecret
    persona: ActivityPersona
    chosen: list[WarmingChannel]
    # Channel → the post ids the channel loop's read just fetched (so a poll vote or a
    # forward only ever touches a post this account has actually "seen").
    recent_ids: dict[str, list[int]]
    tally: _ChannelTally
    remaining_actions: int | None
    # Every channel warming joined for this account, left ones included (the chat
    # extras pick among the not-left ones; the cycle reads ``left_at`` for cooldown).
    joined: list[JoinedChannel] = field(default_factory=list)
    # The account row, for facts only a session check knows (``premium``; ``None`` =
    # unknown, and an unknown Premium is never probed by writing).
    account: AccountRead | None = None
    # Second budget beside ``tally.attempts``: bytes the media extras may still pull this
    # cycle. Debited before dispatch like attempts; ``0`` (the default) disables them.
    media_bytes_left: int = 0

    def can_attempt(self) -> bool:
        """The cycle's own daily-budget predicate (``_cycle._can_attempt``), verbatim."""
        if self.remaining_actions is None:
            return True
        return self.tally.attempts < self.remaining_actions


@dataclass(frozen=True)
class _ExtraSpec:
    key: str  # the ``ExtraToggles`` key — snake_case of the tuning-card row
    kind: _ExtraKind
    run: _Runner
    needs: frozenset[_Need] = frozenset()


async def _write(ctx: _ExtraContext, action: TelegramAction) -> ActionResult:
    """Dispatch a budget-booking extra. Book first — hard invariant, see module doc."""
    ctx.tally.attempts += 1
    return await _seams.execute(ctx.account_id, action)


def _recent_posts(ctx: _ExtraContext) -> tuple[str, list[int]]:
    """A channel whose read fetched posts, plus up to five of them (``needs`` guarantees one)."""
    channel = _seams.rng.choice([c for c, ids in ctx.recent_ids.items() if ids])
    return channel, ctx.recent_ids[channel][:_POST_IDS_MAX]
