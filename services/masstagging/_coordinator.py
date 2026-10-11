"""One run: lay the list out, one task per account, rest until tomorrow, settle.

A pass runs every working account in parallel until each has nothing left, has spent
its day, or dropped out. Then:

* nothing left in work → ``done``;
* an account spent its day → the campaign rests until the next day begins and goes on
  by itself (``resting``);
* an account reached the operator's "stop after N" → the campaign pauses;
* every account caught floods in a row → it stops (``failed``: the operator continues
  later or adds accounts);
* nobody can work at all (spam block, a dead session, busy elsewhere) → ``failed``.
"""

from __future__ import annotations

import asyncio
from typing import TYPE_CHECKING, Literal

from core.config import settings
from core.logging import log_event
from core.repositories import masstagging as repository
from core.telegram_client import TelegramReadError
from schemas.telegram_actions_masstagging import ListMasstagContacts, MasstagContactsResult
from services import _account_owner
from services.masstagging import _fleet, _moves, _seams, _worker
from services.masstagging._context import load_context
from services.masstagging.pace import next_day_start
from services.masstagging.targets import contact_seed, seeds_of

if TYPE_CHECKING:
    from schemas.masstagging_records import TargetSeed
    from services.masstagging._context import RunContext
    from services.masstagging._fleet import FleetAccount

RunOutcome = Literal["done", "volume", "floods", "no_accounts"]
_OWNER = "masstagging"


async def run_campaign(campaign_id: str, run_id: str, *, fresh: bool) -> RunOutcome:
    ctx = await load_context(campaign_id, run_id)
    if ctx is None:
        return "done"
    if fresh:
        seeds = await _layout(ctx, await live_accounts(campaign_id))
        await repository.replace_targets(campaign_id, seeds, at=_seams.now())
    elif ctx.settings.target_source != "contacts":
        await repository.merge_targets(campaign_id, seeds_of(ctx.settings.targets), at=_seams.now())
    while True:
        record = await repository.fetch_campaign(campaign_id)
        if record is None:
            return "done"
        if record.rest_until_unix is not None:
            await _rest(campaign_id, record.rest_until_unix)
            continue
        outcome = await _pass(ctx)
        if outcome == "again":
            continue
        if outcome != "rest":
            return outcome
        until = next_day_start(_seams.now())
        await repository.set_runtime(campaign_id, rest_until_unix=until)
        await log_event(
            "INFO", "masstagging_run_resting", extra={"campaign_id": campaign_id, "until": until}
        )


async def live_accounts(campaign_id: str, halted: set[str] | None = None) -> list[FleetAccount]:
    """Roster accounts that may work AND are held by this run, in roster order."""
    roster = [a for a in await repository.list_roster(campaign_id) if a not in (halted or set())]
    fleet = await _fleet.fleet_of(roster)
    return [
        fleet[account_id]
        for account_id in roster
        if fleet[account_id].usable
        and _account_owner.owner_of(account_id) == _OWNER
        and _account_owner.holder_of(account_id) == campaign_id
    ]


async def _layout(ctx: RunContext, live: list[FleetAccount]) -> list[TargetSeed]:
    """The list as saved, or each account's contacts (the first account to know one wins)."""
    if ctx.settings.target_source != "contacts":
        return seeds_of(ctx.settings.targets)
    seeds: dict[str, TargetSeed] = {}
    for account in live:
        try:
            result = await _seams.execute_read(account.account_id, ListMasstagContacts())
        except TelegramReadError:
            await log_event(
                "WARNING",
                "masstagging_contacts_unavailable",
                account_id=account.account_id,
                extra={"campaign_id": ctx.campaign_id},
            )
            continue
        if not isinstance(result, MasstagContactsResult):  # pragma: no cover - read contract
            continue
        for user in result.users:
            if not user.is_bot and not user.is_deleted:
                seeds.setdefault(f"id:{user.user_id}", contact_seed(user, account.account_id))
    return list(seeds.values())


async def _rest(campaign_id: str, until: int) -> None:
    """Sleep until tomorrow in slices, then clear the rest and go on."""
    tick = settings.masstagging.rest_poll_seconds
    while (remaining := until - _seams.now()) > 0:
        await _seams.sleep(min(remaining, tick))
    _seams.assert_live_run()
    await repository.set_runtime(campaign_id, rest_until_unix=None)


async def _pass(ctx: RunContext) -> RunOutcome | Literal["rest", "again"]:
    """Every working account until it stops, and what the campaign does next."""
    live = await live_accounts(ctx.campaign_id, ctx.halted)
    if not live:
        return "no_accounts" if await _anything_left(ctx) else "done"
    tasks = [asyncio.create_task(_worker.work(ctx, account)) for account in live]
    try:
        outcomes = await asyncio.gather(*tasks)
    finally:
        for task in tasks:
            task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
    return await _after_pass(ctx, outcomes)


async def _after_pass(
    ctx: RunContext, outcomes: list[_worker.WorkerOutcome]
) -> RunOutcome | Literal["rest", "again"]:
    if not await _anything_left(ctx):
        return "done"
    if all(outcome == "flood_out" for outcome in outcomes):
        return "floods"
    if "resting" in outcomes or "flood_out" in outcomes:
        return "rest"
    if "volume" in outcomes:
        return "volume"
    # An account finished while another dropped out and gave its people back.
    still = await live_accounts(ctx.campaign_id, ctx.halted)
    targets = await repository.list_targets(ctx.campaign_id)
    if any(
        t.state in _moves.PICKABLE and _worker.takes(t, account.account_id)
        for t in targets
        for account in still
    ):
        return "again"
    return "no_accounts"


async def _anything_left(ctx: RunContext) -> bool:
    targets = await repository.list_targets(ctx.campaign_id)
    return any(t.state in _moves.ACTIVE_STATES for t in targets)
