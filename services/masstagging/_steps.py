"""One step of one account: who the people are, their dialogs, the story or the toggle.

The worker hands a batch it already claimed (``working``). Each person is resolved
(at most once per account — an id is per session), checked against the registry, then:

* **priming** — the auto-delete toggle, and that is the tag;
* **stories** — the toggle first when «Сначала создать диалог» is on (the dialog makes
  the push arrive), then ONE story mentioning everyone still standing.

An account-wide answer (flood, spam block, dead session, a lost connection, the day's
stories spent) stops the step with a signal; whatever the step did not finish goes back
to wait, and the worker decides what the account does next.
"""

from __future__ import annotations

from pathlib import PurePath
from typing import TYPE_CHECKING

from core.config import settings
from core.logging import log_event
from core.masstagging_media import VIDEO_SUFFIXES, MasstaggingMediaError, read_media
from core.repositories import masstagging as repository
from core.telegram_client import TelegramReadError
from schemas.masstagging_records import JournalRecord
from schemas.telegram_actions import PostStory
from schemas.telegram_actions_masstagging import (
    MasstagResolvedTarget,
    MasstagToggleTtl,
    MasstagUser,
    ResolveMasstagTarget,
)
from schemas.telegram_actions_media import StoryLink, StoryMention
from services.masstagging import _moves, _seams, _state
from services.masstagging._verdicts import Verdict, classify
from services.masstagging.pace import caption_limit, day_of, lifetime_seconds
from services.masstagging.targets import member_seed

if TYPE_CHECKING:
    from schemas.masstagging_records import TargetRecord
    from services.masstagging._context import RunContext
    from services.masstagging.pace import AccountView

_DAY = 86_400


class StepSignal(Exception):  # noqa: N818 - a control-flow signal, not an error
    """An account-wide answer: the worker decides what the account does next."""

    def __init__(self, verdict: Verdict, seconds: int | None = None) -> None:
        self.verdict = verdict
        self.seconds = seconds
        super().__init__(verdict)


def _user_of(target: TargetRecord) -> MasstagUser | None:
    if target.resolved_user_id is None:
        return None
    return MasstagUser(
        user_id=target.resolved_user_id, username=target.username, first_name=target.first_name
    )


async def _lookup(account_id: str, target: TargetRecord) -> MasstagResolvedTarget:
    await repository.bump_day(account_id, day_of(_seams.now()), resolves=1)
    lookup = target.raw if target.kind == "phone" else (target.raw.removeprefix("@"))
    limit = settings.masstagging.chat_members_limit if target.kind == "username" else 0
    try:
        result = await _seams.execute_read(
            account_id, ResolveMasstagTarget(target=lookup, members_limit=limit)
        )
    except TelegramReadError as exc:
        if exc.kind in {"flood_wait", "unavailable"}:
            verdict: Verdict = "flood" if exc.kind == "flood_wait" else "reconnect"
            raise StepSignal(verdict, exc.seconds) from exc
        return MasstagResolvedTarget(outcome="not_found")
    if not isinstance(result, MasstagResolvedTarget):  # pragma: no cover - the read's contract
        return MasstagResolvedTarget(outcome="not_found")
    return result


async def _expand(
    ctx: RunContext, account_id: str, target: TargetRecord, members: list[MasstagUser]
) -> None:
    """A group link: its members take its place in the list."""
    people = [user for user in members if not user.is_bot and not user.is_deleted]
    added = await repository.add_targets(
        ctx.campaign_id, [member_seed(user, account_id) for user in people], at=_seams.now()
    )
    await _moves.skip(ctx, target, "chat_expanded", account_id)
    await repository.add_events(
        ctx.campaign_id,
        [
            JournalRecord(
                target_key=target.target_key,
                event="expanded",
                at_unix=_seams.now(),
                account_id=account_id,
                detail=str(added),
            )
        ],
    )


async def resolve(ctx: RunContext, account_id: str, target: TargetRecord) -> TargetRecord | None:
    """The target with who they are, or ``None`` once it has been skipped instead."""
    if target.resolved_by == account_id and target.resolved_user_id is not None:
        return target
    if target.kind == "contact" or (target.kind == "link" and target.username is None):
        # Known only to the account that found them; the worker never hands it elsewhere.
        return target if target.resolved_user_id is not None else None
    found = await _lookup(account_id, target)
    if found.outcome == "group":
        await _expand(ctx, account_id, target, found.members)
        return None
    if found.outcome != "user" or found.user is None:
        reason = {"hidden": "chat_hidden", "not_user": "not_a_user"}.get(
            found.outcome, "phone_not_found" if target.kind == "phone" else "username_not_found"
        )
        await _moves.skip(ctx, target, reason, account_id)  # ty: ignore[invalid-argument-type]
        return None
    if found.user.is_bot or found.user.is_deleted:
        await _moves.skip(
            ctx, target, "bot" if found.user.is_bot else "deleted_account", account_id
        )
        return None
    return await _moves.resolved(ctx, target, found.user, account_id)


async def toggle(ctx: RunContext, account_id: str, target: TargetRecord) -> bool:
    """The auto-delete flick in the person's DM; ``False`` when the person failed."""
    limits = settings.masstagging
    hold = _seams.rng.uniform(limits.ttl_hold_min_seconds, limits.ttl_hold_max_seconds)
    peer = str(target.resolved_user_id)
    result = await _seams.execute(account_id, MasstagToggleTtl(peer=peer, hold_seconds=hold))
    verdict = classify(result)
    if verdict == "ok":
        await repository.bump_day(account_id, day_of(_seams.now()), dialogs=1)
        return True
    if verdict in {"privacy", "gone", "unconfirmed", "error", "premium", "too_much"}:
        reason = {"privacy": "privacy", "gone": "deleted_account", "unconfirmed": "unconfirmed"}
        await _moves.fail(ctx, target, reason.get(verdict, "error"), account_id)  # ty: ignore[invalid-argument-type]
        return False
    raise StepSignal(verdict, result.flood_wait_seconds)


def _fit_caption(caption: str, mentions: list[StoryMention], limit: int) -> str:
    """The operator's caption, cut so the mentions still fit under the account's limit."""
    tail = " ".join(f"@{m.username}" if m.username else (m.first_name or "•") for m in mentions)
    room = max(0, limit - len(tail) - 1)
    return caption.strip()[:room]


async def _story_action(
    ctx: RunContext, view: AccountView, people: list[TargetRecord]
) -> PostStory | None:
    media = ctx.settings.media
    item = media[_state.next_media(ctx.campaign_id, len(media))]
    try:
        content = await read_media(item.media_id)
    except MasstaggingMediaError:
        return None
    suffix = PurePath(item.media_id).suffix
    mentions = [
        StoryMention(
            user_id=t.resolved_user_id or 0, username=t.username, first_name=t.first_name[:64]
        )
        for t in people
    ]
    link = None
    if view.premium and ctx.settings.link:
        link = StoryLink(url=ctx.settings.link, text=ctx.settings.link_text)
    return PostStory(
        filename=f"story{suffix}",
        content=content,
        media_kind="video" if suffix in VIDEO_SUFFIXES else "image",
        caption=_fit_caption(ctx.settings.caption, mentions, caption_limit(view)),
        privacy_preset="public" if ctx.settings.visibility == "all" else "contacts",
        period_seconds=lifetime_seconds(view, ctx.settings.lifetime_hours),
        mentions=mentions,
        link=link,
    )


async def post_story(
    ctx: RunContext, account_id: str, view: AccountView, people: list[TargetRecord]
) -> None:
    """One story mentioning ``people``; they are tagged when it is up."""
    action = await _story_action(ctx, view, people)
    if action is None:
        for target in people:
            await _moves.fail(ctx, target, "error", account_id)
        return
    keys = [target.target_key for target in people]
    # Written before the call: a restart mid-call must not post the same story again.
    await repository.update_targets(
        ctx.campaign_id, keys, at=_seams.now(), expect_states=("working",), reason="unconfirmed"
    )
    result = await _seams.execute(account_id, action)
    verdict = classify(result)
    if verdict == "premium":
        retry = action.model_copy(update={"link": None, "period_seconds": _DAY})
        result = await _seams.execute(account_id, retry)
        verdict = classify(result)
    if verdict == "ok":
        await repository.bump_day(account_id, day_of(_seams.now()), stories=1)
        await _moves.tagged(ctx, people, account_id, story_id=result.message_id)
        await log_event(
            "INFO",
            "masstagging_story_posted",
            account_id=account_id,
            extra={"campaign_id": ctx.campaign_id, "mentions": len(people)},
        )
        return
    if verdict in {"unconfirmed", "error", "privacy", "gone"}:
        reason = "unconfirmed" if verdict == "unconfirmed" else "error"
        for target in people:
            await _moves.fail(ctx, target, reason, account_id)
        return
    await repository.update_targets(
        ctx.campaign_id, keys, at=_seams.now(), expect_states=("working",), reason=None
    )
    raise StepSignal(verdict, result.flood_wait_seconds)
