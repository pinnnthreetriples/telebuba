"""Who may speak in one target: the voices it still listens to, and whether it opened.

Split from ``_steps`` for the file-size budget, along the one seam that is also a
dependency direction: nothing here reserves, sends or settles anything. ``_steps`` asks
these questions and then acts on the answers.

Three of them. :func:`pick_account` deals a step to an account of its role that is
inside the chat, still in the run, and not one the chat has refused. :func:`opening_role`
and :func:`opening_stands` are the two halves of one rule — nothing is played on top of
a dialogue that never opened — asked before a target is entered and before each line.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING, NamedTuple

from core.config import settings
from core.logging import log_event
from core.repositories import neuroshilling as repository
from services import pacing
from services.neuroshilling import _dispatch, _seams

if TYPE_CHECKING:
    from schemas.neuroshilling import NeuroshillingCampaign
    from schemas.neuroshilling_scenario import NeuroshillingStep
    from services.neuroshilling._context import RunContext


class Voices(NamedTuple):
    """Who is inside one target, and who of them it has stopped listening to.

    ``chats`` is ``account_id -> chat_id`` and outlives the call: a revive run enters
    its chat once and acts in it cycle after cycle. ``silenced`` is the accounts this
    chat refused a write from, and it lasts for ONE telling of the dialogue —
    ``engine._act`` makes a fresh one each time — so a mute that was a ten-minute
    restriction does not cost the account every later cycle.
    """

    chats: dict[str, int]
    silenced: set[str]


def speaks(campaign: NeuroshillingCampaign, step: NeuroshillingStep) -> bool:
    """Whether this step puts a message into the chat at all."""
    if step.kind != "message":
        return False
    return bool(step.text.strip()) or _dispatch.media_source(campaign, step) is not None


def opening_role(context: RunContext, step: NeuroshillingStep) -> str | None:
    """The role ``step`` speaks for when it is the dialogue's first line, else ``None``.

    ``engine._play_target`` asks before it pays the settle pause and the first step's
    delay: a target where nobody of that role got in cannot open, and finding that out
    through ``_steps.play_step`` costs minutes per dead chat.
    """
    first = next((item for item in context.steps if speaks(context.campaign, item)), None)
    if first is None or first.step_id != step.step_id:
        return None
    return step.role_id or ""


async def opening_stands(context: RunContext, target: str, step: NeuroshillingStep) -> bool:
    """Whether this pass has said anything here for ``step`` to follow.

    A staged dialogue is a question and what comes after it. With the question lost —
    its speaker was refused, or never got into the chat — everything after it is an
    answer to nothing, posted by a stranger into somebody's group. So a step that is
    not the opening one plays only once this pass has a message in the target.

    Asked of the journal and not of a flag, because the case that needs it most has no
    flag left: a resumed run is handed the steps that have no row yet, which for a
    target given up on at its first line is every step but the first.

    A ``revive`` run is not asked. Its one chat is the operator's own and the point of
    the mode is that the room is never quiet, so a round that lost its first line still
    says the rest — and a run whose opening role is halted for good would otherwise go
    on cycling, and pausing, and publishing nothing at all.
    """
    if context.campaign.mode == "revive":
        return True
    earlier = (item for item in context.steps if item.position < step.position)
    if not any(speaks(context.campaign, item) for item in earlier):
        return True
    if await repository.has_sent_message(context.run_id, target):
        return True
    await log_event(
        "WARNING",
        "neuroshilling_dialogue_not_opened",
        extra={
            "campaign_id": context.campaign.campaign_id,
            "target": target,
            "position": step.position,
        },
    )
    return False


def step_delay(step: NeuroshillingStep) -> float:
    limits = settings.neuroshilling
    return pacing.human_delay(
        step.delay_min_seconds,
        step.delay_max_seconds,
        rng=_seams.rng,
        mu=limits.delay_lognorm_mu,
        sigma=limits.delay_lognorm_sigma,
    )


async def pick_account(
    context: RunContext,
    step: NeuroshillingStep,
    voices: Voices,
) -> str | None:
    """The least-busy account of this step's role that still has a voice in this chat.

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
        if account_id not in context.halted
        and account_id in voices.chats
        and account_id not in voices.silenced
    ]
    if not candidates:
        return None
    since = (datetime.now(UTC) - timedelta(hours=1)).isoformat()
    counts = await repository.count_messages_since(candidates, since)
    fewest = min(counts.get(account_id, 0) for account_id in candidates)
    idlest = [account_id for account_id in candidates if counts.get(account_id, 0) == fewest]
    return _seams.rng.choice(idlest)
