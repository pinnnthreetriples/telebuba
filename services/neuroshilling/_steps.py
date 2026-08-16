"""One step of one target: who speaks it, and the reservation that lets them.

**The row goes in first.** :func:`play_step` writes a ``pending`` journal row through
``claim_message`` BEFORE anything is dispatched. Sending first and writing second leaves
a window in which Telegram has the message and SQLite has nothing, so the next boot
replays the step into a chat that already has it — the unique index cannot help, because
it only protects rows that exist. The claim's ``False`` return is also what makes a
resumed run walk past work it already did.

**The quota re-count and the insert share one lock.** Roles belong to the campaign, so
the same account plays the same role in every target, and two campaigns may share an
account outright: without the lock both read an under-cap count and both publish.

That lock is this module's own private map, and it is RELEASED before anything is
dispatched — :func:`_reserve` closes its ``async with`` and the send happens after it
returns. So the quota lock and ``services.warming.account_lock`` (which
``_seams.execute`` takes) are never held at the same time, in either order. The
alternative, holding the quota lock across the send, would nest it outside a LIFECYCLE
mutex that ``services.accounts.lifecycle`` keeps across several awaits — which is a
deadlock waiting for an operator to press remove.

Publishing the reserved step is ``_dispatch``'s half; this module stops at the row.
"""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING, Final, NamedTuple, get_args

from core.config import settings
from core.logging import log_event
from core.repositories import neuroshilling as repository
from schemas.neuroshilling import NeuroshillingStepKey
from schemas.telegram_actions import ReactToMessage
from schemas.telegram_actions_chat import ChatReactionEmoji
from services import pacing
from services.neuroshilling import _dispatch, _seams

if TYPE_CHECKING:
    from schemas.neuroshilling import NeuroshillingCampaign
    from schemas.neuroshilling_scenario import NeuroshillingStep
    from services.neuroshilling._context import RunContext

_REACTIONS: Final[frozenset[ChatReactionEmoji]] = frozenset(get_args(ChatReactionEmoji))

# One lock per account serialises its [re-count quota -> insert pending row] section.
# A plain dict needs no lock of its own: one uvicorn worker means one event loop, and
# ``asyncio.Lock`` binds to the running loop, so tests clear this between cases.
_ACCOUNT_LOCKS: dict[str, asyncio.Lock] = {}


def _account_lock(account_id: str) -> asyncio.Lock:
    lock = _ACCOUNT_LOCKS.get(account_id)
    if lock is None:
        lock = _ACCOUNT_LOCKS[account_id] = asyncio.Lock()
    return lock


def reset_for_tests() -> None:
    _ACCOUNT_LOCKS.clear()


class _Reserved(NamedTuple):
    """The result of the locked [re-count -> insert] section."""

    claimed: bool
    quota_reason: str | None


async def play_step(
    context: RunContext,
    target: str,
    step: NeuroshillingStep,
    chats: dict[str, int],
) -> bool:
    """Play one step into one target. ``False`` means stop playing this target.

    The delay comes first and the speaker is picked after it, so selection scores the
    hourly load as it is at the moment of sending rather than as it was a minute ago.
    """
    await _seams.sleep(_step_delay(step))
    account_id = await _pick_account(context, step, chats)
    if account_id is None:
        await log_event(
            "WARNING",
            "neuroshilling_role_exhausted",
            extra={"campaign_id": context.campaign.campaign_id, "target": target},
        )
        return False
    if step.kind == "reaction":
        return await _play_reaction(context, target, step, account_id, chats[account_id])
    return await _play_message(context, target, step, account_id, chats[account_id])


def _step_delay(step: NeuroshillingStep) -> float:
    limits = settings.neuroshilling
    return pacing.human_delay(
        step.delay_min_seconds,
        step.delay_max_seconds,
        rng=_seams.rng,
        mu=limits.delay_lognorm_mu,
        sigma=limits.delay_lognorm_sigma,
    )


async def _pick_account(
    context: RunContext,
    step: NeuroshillingStep,
    chats: dict[str, int],
) -> str | None:
    """The least-busy account of this step's role that is inside this chat.

    Least busy over the rolling hour rather than over the run, because the cap it is
    spreading load against is a rolling hour of the ACCOUNT's whole history. Ties break
    through the rng seam, the same way neurocomment's selection does: a deterministic
    tie-break concentrates a whole dialogue on one session.

    ``count_messages_since`` scores a FAILED attempt as load too, which is what stops an
    account that cannot send in this chat from staying at zero and being dealt every
    remaining step of the dialogue while its working sibling sits idle.
    """
    candidates = [
        account_id
        for account_id in context.by_role.get(step.role_id or "", ())
        if account_id not in context.halted and account_id in chats
    ]
    if not candidates:
        return None
    since = (datetime.now(UTC) - timedelta(hours=1)).isoformat()
    counts = await repository.count_messages_since(candidates, since)
    fewest = min(counts.get(account_id, 0) for account_id in candidates)
    idlest = [account_id for account_id in candidates if counts.get(account_id, 0) == fewest]
    return _seams.rng.choice(idlest)


async def _reserve(
    context: RunContext,
    target: str,
    step: NeuroshillingStep,
    account_id: str,
    text: str,
) -> _Reserved:
    """Re-count the caps and insert the row, both under this account's lock.

    Everything between the count and the insert is inside the lock and there is no
    other await in it, which is what makes a serialised sibling see this row and stop
    at the cap instead of stacking past it.
    """
    async with _account_lock(account_id):
        reason = await _quota_reason(context.campaign, account_id, target)
        claimed = await repository.claim_message(
            _key(context, target, step),
            campaign_id=context.campaign.campaign_id,
            account_id=account_id,
            text=text,
            # A refused step reserves its key through THIS insert rather than a second
            # one, so the resumed run treats it as played and the dialogue moves on.
            status="skipped" if reason else "pending",
        )
    return _Reserved(claimed=claimed, quota_reason=reason)


def _key(context: RunContext, target: str, step: NeuroshillingStep) -> NeuroshillingStepKey:
    return NeuroshillingStepKey(run_id=context.run_id, target=target, step_id=step.step_id)


async def _quota_reason(
    campaign: NeuroshillingCampaign,
    account_id: str,
    target: str,
) -> str | None:
    """Which of the campaign's three ceilings this account has reached, if any.

    Counted over MESSAGE steps only, which is what the operator's fields say: a
    reaction is not a message and gating one against ``messages_per_hour`` would make
    the number in the form mean something it does not say.
    """
    now = datetime.now(UTC)
    usage = await repository.read_quota_usage(
        campaign.campaign_id,
        account_id,
        target,
        hour_since=(now - timedelta(hours=1)).isoformat(),
        day_since=(now - timedelta(days=1)).isoformat(),
    )
    if usage.hour >= campaign.messages_per_hour:
        return "quota_hour"
    if 0 < campaign.messages_per_chat_per_day <= usage.chat_day:
        return "quota_day"
    total = campaign.total_per_account
    if total is not None and usage.campaign_total >= total:
        return "quota_total"
    return None


async def _play_message(
    context: RunContext,
    target: str,
    step: NeuroshillingStep,
    account_id: str,
    chat_id: int,
) -> bool:
    """Reserve, vet, publish and settle one message step."""
    text = step.text.strip()
    carries_media = _dispatch.media_source(context.campaign, step) is not None
    if not text and not carries_media:
        return True
    reserved = await _reserve(context, target, step, account_id, text)
    if not reserved.claimed:
        return True
    if reserved.quota_reason is not None:
        await log_event(
            "WARNING",
            "neuroshilling_step_skipped_quota",
            account_id=account_id,
            extra={"target": target, "reason": reserved.quota_reason},
        )
        return True
    if not await _dispatch.vet_text(context, target, step, account_id, text):
        return True
    reply_to = await _dispatch.resolve_reply_to(context, target, step)
    action = _dispatch.build_message_action(context.campaign, step, chat_id, text, reply_to)
    return await _dispatch.dispatch(context, target, step, account_id, action)


async def _play_reaction(
    context: RunContext,
    target: str,
    step: NeuroshillingStep,
    account_id: str,
    chat_id: int,
) -> bool:
    """React to an earlier step's message. Skipped, never failed, when it cannot aim.

    The anchor is NOT walked up the chain the way a reply's is: a reply that lands one
    message higher still reads as the conversation it belongs to, whereas a reaction
    placed on a different message than the operator chose is simply the wrong reaction.
    No quota either — the ceilings are counted in messages, and this is not one.
    """
    anchor = context.by_position.get(step.target_position or 0)
    message_id = (
        None if anchor is None else await repository.fetch_message_id(_key(context, target, anchor))
    )
    # Looked up in the allowed set rather than compared against it, because the stored
    # column is free text: a row written before the set was narrowed must skip the step
    # rather than reach the gateway as an emoji it will refuse.
    emoji = next((allowed for allowed in _REACTIONS if allowed == step.emoji), None)
    if message_id is None or emoji is None:
        await log_event(
            "INFO",
            "neuroshilling_step_skipped_reaction",
            account_id=account_id,
            extra={"target": target, "position": step.position},
        )
        return True
    if not await repository.claim_message(
        _key(context, target, step),
        campaign_id=context.campaign.campaign_id,
        account_id=account_id,
        text="",
    ):
        return True
    action = ReactToMessage(chat_id=chat_id, message_id=message_id, emoji=emoji)
    return await _dispatch.dispatch(context, target, step, account_id, action)
