"""Unlink a channel Telegram says has its comments switched off.

A broadcast channel with no linked discussion group can never be commented on: there is
no chat to join and no message to reply to. The verdict was logged and cached and then
ignored — the channel sat in its campaign forever, re-resolved and re-reported on every
onboarding pass, while the board badged it grey and nobody was working on a recovery
that cannot exist.

Its own module because two unrelated surfaces reach the same verdict — the live resolve
in ``_onboard_pair`` and the operator's "check channels" probe in ``bans`` — and both
must have the same consequence. A rule that fires only on the path you happened to take
is the bug this one exists to close.

Only ONE of those surfaces may reach it directly. ``bans``'s ban probe reports
``comments_disabled`` whenever the linked group cannot be RESOLVED
(``core.telegram_client._read._resolve_linked_group_entity`` → None): an account that
never onboarded into that chat, one just kicked out of it, and a FloodWait on the entity
read all land there. Acting on it would unlink a channel the other accounts comment in
fine — and ``_deactivate_channel`` deletes every per-account pin on that channel, which
nothing restores. So the probe re-reads through ``_onboard_pair._safe_resolve``, which
asks ``full_chat.linked_chat_id`` directly, and only that answer reaches this module.
"""

from __future__ import annotations

from core.db import fetch_active_campaign_for_channel
from core.logging import log_event


async def report_and_drop(channel: str, account_id: str) -> None:
    """The whole consequence of a comments-off verdict: say it, then act on it.

    Two lines, in this order, because that is how the operator reads the terminal — the
    verdict and the thing it cost. The second is skipped when the channel is linked to no
    ACTIVE campaign: the verdict belongs to the channel and is still worth saying, but
    there is nothing left to unlink.

    ERROR, not INFO: green read as "noted, carry on" for a dead end that repeated on
    every pass, and the only remedy (turn comments on, or drop the channel) is an action.

    ``account_id`` is whichever session read the verdict — carried into both lines so they
    sit with that account's other rows, not because it had anything to do with the
    verdict: comments-off is a property of the channel.

    The unlink goes through the service rather than the repository, exactly like the three
    sibling auto-drops (``_rejoin._drop_channel_if_nothing_works``,
    ``bans._unlink_channel_if_no_account_left``, ``_sweep._drop_unapproved_channel``):
    that is what makes a running listener reconcile and stop watching the channel.
    """
    await log_event(
        "ERROR",
        "neurocomment_channel_comments_off",
        account_id=account_id,
        extra={"channel": channel},
    )
    campaign = await fetch_active_campaign_for_channel(channel)
    if campaign is None:
        return
    # Late import: ``campaigns`` reaches ``_runtime``, which reaches the onboarding chain
    # that calls this — the same cycle the sibling rules dodge the same way.
    from services.neurocomment import campaigns as campaigns_service  # noqa: PLC0415

    await campaigns_service.deactivate_channel(campaign.campaign_id, channel)
    await log_event(
        "ERROR",
        "neurocomment_channel_comments_off_dropped",
        account_id=account_id,
        extra={
            "channel": channel,
            "campaign_id": campaign.campaign_id,
            "reason": "comments_off",
        },
    )
