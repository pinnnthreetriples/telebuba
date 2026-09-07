"""Shared vocabulary of the warming *extras* step — context, spec and the two dispatchers.

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

from dataclasses import dataclass
from typing import TYPE_CHECKING, Literal

from services.warming import _seams

if TYPE_CHECKING:
    from collections.abc import Awaitable, Callable

    from schemas.accounts import AccountRead
    from schemas.telegram_actions import ActionResult, TelegramAction
    from schemas.warming import ActivityPersona, WarmingChannel, WarmingSettingsSecret
    from services.warming._steps import _ChannelTally

    # ``None`` = not applicable this cycle: nothing was dispatched, nothing booked.
    _Runner = Callable[["_ExtraContext"], Awaitable[ActionResult | None]]

_ExtraKind = Literal["read", "write"]
# Facts a spec may require before it is eligible — data, so one ``_is_eligible``
# answers for the whole registry instead of a predicate lambda per spec.
_Need = Literal["recent_ids", "joined", "premium", "media_bytes"]


@dataclass
class _ExtraContext:
    """Everything an extra may look at: the cycle's account, channels and running tally."""

    account_id: str
    account: AccountRead | None
    secret: WarmingSettingsSecret
    persona: ActivityPersona
    chosen: list[WarmingChannel]
    # Channel → the post ids the channel loop's read just fetched (so a poll vote or a
    # forward only ever touches a post this account has actually "seen").
    recent_ids: dict[str, list[int]]
    tally: _ChannelTally
    remaining_actions: int | None

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


async def _read(ctx: _ExtraContext, action: TelegramAction) -> ActionResult:
    """Dispatch a free read — same lease-fenced path, no budget booked."""
    return await _seams.execute(ctx.account_id, action)
