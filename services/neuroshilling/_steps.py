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

**The one thing this module publishes itself is a replay.** When the send came back a
confirmed ACCOUNT ban, ``_substitution`` promotes a reserve account and gets it into the
chat, and the line the banned session lost is said again from the stand-in's — over the
SAME journal row, handed across rather than inserted, because ``(run_id, target,
step_id)`` is unique and the failed row already holds it.
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
from services.neuroshilling import _dispatch, _seams, _substitution

if TYPE_CHECKING:
    from schemas.neuroshilling import NeuroshillingCampaign
    from schemas.neuroshilling_scenario import NeuroshillingStep
    from services.neuroshilling._context import RunContext

_REACTIONS: Final[frozenset[ChatReactionEmoji]] = frozenset(get_args(ChatReactionEmoji))

# The one ban verdict that says anything about the chat it was suffered in.
_ACCOUNT_BANNED: Final = "account_banned"


class _Ban(NamedTuple):
    """An account Telegram finished off, and which of the two verdicts finished it.

    One name rather than two arguments because everything below weighs them together:
    both buy a substitute, and only one of them is evidence about the chat.
    """

    account_id: str
    verdict: str


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
        played = await _play_reaction(context, target, step, account_id, chats[account_id])
    else:
        played = await _play_message(context, target, step, account_id, chats[account_id])
    # Answered here, by the step that put it there, so nothing is ever left in the map
    # for a later step to act on. The ban is already on the roster row either way —
    # ``_dispatch`` wrote it where the verdict was classified — so everything below is
    # only about who says the rest of the dialogue.
    verdict = context.banned.pop(account_id, None)
    if verdict is None:
        return played
    if not context.campaign.reserve_enabled:
        # The operator's switch. With it off the role simply goes a voice short: the
        # account is already in ``halted``, its siblings play the rest of the dialogue,
        # and no reserve is touched.
        return played
    return await _substitute(context, target, step, _Ban(account_id, verdict), chats)


async def _substitute(
    context: RunContext,
    target: str,
    step: NeuroshillingStep,
    ban: _Ban,
    chats: dict[str, int],
) -> bool:
    """Replace a banned speaker and let the stand-in say the line it lost.

    ``False`` when nothing took over, which abandons this target and leaves the rest of
    the campaign's targets to run: the role is a voice short here, but the reason is the
    account rather than anything about the remaining chats.

    A reaction is not replayed. It is journalled but counted in neither the progress
    numerator nor the campaign's ceilings, and the stand-in is in the cast from now on
    either way, so the replay would buy only a second aim at the same anchor.

    A stand-in that loses its own replay is taken out of ``context.banned`` here and
    weighed against the same target. The substitution is not chained: a chat that bans
    on sight would otherwise empty the pool one account at a time, and
    ``record_send_verdict`` has already retired the stand-in across its presence rows.
    """
    if await _chat_bans_the_fleet(context, target, ban):
        return False
    stand_in = await _substitution.substitute(context, target, chats, ban.account_id)
    if stand_in is None:
        return False
    if step.kind == "reaction":
        return True
    played = await _replay(context, target, step, stand_in, chats[stand_in])
    stand_in_verdict = context.banned.pop(stand_in, None)
    if stand_in_verdict is None:
        return played
    await _chat_bans_the_fleet(context, target, _Ban(stand_in, stand_in_verdict))
    # The target is lost whatever the replay itself answered: the role has now spent two
    # accounts on this chat, and a third would be the same bet again.
    return False


async def _chat_bans_the_fleet(context: RunContext, target: str, ban: _Ban) -> bool:
    """Record a ban suffered in ``target`` and answer whether the CHAT is the cause.

    One ban is about the account, which is what buys a reserve. A SECOND ban in the
    same chat is evidence of the correlated fleet condition an account ban cannot
    explain — one proxy subnet, one registration batch — and Telegram does not report
    that as a chat refusal, so nothing else here can tell the difference. Without this
    count a role with five players spends five reserves on one hostile chat, because
    ``account_banned`` is not a verdict that loses the target on its own.

    Only ``account_banned`` counts. ``account_dead`` buys a substitute too — the session
    is finished and somebody has to say the line — but a session that is logged out was
    logged out before it opened this chat, and treating two of them as "this chat bans
    the fleet" abandons a target over something the target had no part in.

    Counted per target for the whole run, so the answer also holds for the substitution
    a later step of the same dialogue would ask for, and for the next revive cycle.
    """
    banned_here = context.banned_in.setdefault(target, set())
    if ban.verdict == _ACCOUNT_BANNED:
        banned_here.add(ban.account_id)
    if len(banned_here) < 2:  # noqa: PLR2004 - the second ban IS the threshold
        return False
    await log_event(
        "WARNING",
        "neuroshilling_chat_bans_the_fleet",
        account_id=ban.account_id,
        extra={"campaign_id": context.campaign.campaign_id, "target": target},
    )
    return True


async def _replay(
    context: RunContext,
    target: str,
    step: NeuroshillingStep,
    account_id: str,
    chat_id: int,
) -> bool:
    """Send the failed step again from the stand-in's session, over its own row.

    The quota is re-counted against the STAND-IN under its own lock, exactly as a first
    attempt would be: the ceilings belong to the account, and handing a row over moves
    it from one account's hourly tally to another's.

    The dedup reservation was given back when the ban settled the row, so the vet runs
    from scratch — including its answer for a text this chat has genuinely had before.

    The line goes out UNATTACHED, and never through ``_dispatch.resolve_reply_to``. The
    anchor is a message id read from the journal, and the stand-in has just joined a
    chat whose history it may not be shown at all — the standard anti-spam setting on
    the groups this targets — where Telegram answers ``MESSAGE_ID_INVALID`` to a reply
    aimed at it. That is not one of ``_telegram._ERROR_VERDICTS``, so it would settle as
    a generic failure and the line would vanish for a reason nothing recorded. Sending
    it unattached is the same degradation ``resolve_reply_to`` already falls back to
    when an anchor is lost, and it logs the same code.
    """
    async with _account_lock(account_id):
        reason = await _quota_reason(context.campaign, account_id, target)
        handed = reason is None and await repository.hand_over_message(
            _key(context, target, step),
            account_id=account_id,
        )
    if reason is not None:
        await log_event(
            "WARNING",
            "neuroshilling_step_skipped_quota",
            account_id=account_id,
            extra={"target": target, "reason": reason},
        )
        return True
    if not handed:
        # The row is no longer ``failed`` — settled by the boot sweep, or by a stop —
        # so the reserve has been spent, the stand-in has joined, and this line is
        # dropped. The one outcome of the whole path that nothing else would report.
        await log_event(
            "WARNING",
            "neuroshilling_replay_dropped",
            account_id=account_id,
            extra={"target": target, "position": step.position},
        )
        return True
    text = step.text.strip()
    if not await _dispatch.vet_text(context, target, step, account_id, text):
        return True
    if step.reply_to_position is not None:
        await log_event(
            "WARNING",
            "neuroshilling_reply_anchor_lost",
            extra={"target": target, "position": step.position},
        )
    action = _dispatch.build_message_action(context.campaign, step, chat_id, text, None)
    return await _dispatch.dispatch(context, target, step, account_id, action)


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
