"""The rule for a channel that will not let us write (#147).

K consecutive write failures on a channel end a *round*: it is paused for a flat
``channel_pause_hours``, in which nothing posts there and no account is onboarded to it,
and its round counter goes up. Once the counter reaches ``channel_max_rounds`` the
channel leaves its campaign instead of pausing again. A delivered comment clears both —
the channel demonstrably works.

The escalating 1h→2h→…→24h back-off this replaced only delayed the verdict; four flat
days actually reach one. Round counter and deadline are persisted on the campaign link
(migration #42) rather than kept in module dicts: the live app restarted 7 times in
three days, so a four-day rule built on memory never reached round 4. Only the
consecutive-failure *window* stays in memory (``_state``) — losing it on a restart costs
at most one round boundary, not the verdict.

Its own module because ``_generate`` is at the file-size cap and because this is a
distinct concern from generating a comment: ``_generate`` classifies one post's outcome,
this decides the channel's fate across days.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from core.config import settings
from core.db import bump_channel_pause, clear_channel_pause
from core.logging import log_event
from services.neurocomment import _state


async def register_write_failure(channel: str, account_id: str) -> None:
    """Count a write failure on ``channel``; end a round when it reaches K.

    The counter is per-CHANNEL, but ``account_id`` is the account whose failure ended the
    round: the neurocomment feed is read one line per account action, and a row with no
    account is the one an operator can't act on.

    No account leaves the chat on the way out, unlike a confirmed personal ban: this is
    the channel forbidding comments, membership costs nothing, and re-joining would spend
    the rolling-24h join cap.
    """
    nc = settings.neurocomment
    if not _state.register_write_failure(
        channel, min_failures=nc.channel_challenge_backoff_min_failures
    ):
        return
    until = datetime.now(UTC) + timedelta(hours=nc.channel_pause_hours)
    pause = await bump_channel_pause(channel, until.isoformat())
    if pause is None:  # the channel lost its active link meanwhile — nothing to pause.
        return
    if pause.pause_rounds >= nc.channel_max_rounds:
        # Late import: ``campaigns`` reaches back here through _runtime -> engine.
        from services.neurocomment import campaigns as campaigns_service  # noqa: PLC0415

        # Via the service, not the repository, so the listener reconciles and stops
        # watching the channel (mirrors ``_sweep._drop_unapproved_channel``).
        await campaigns_service.deactivate_channel(pause.campaign_id, channel)
        await log_event(
            "WARNING",
            "neurocomment_channel_dropped",
            account_id=account_id,
            extra={
                "channel": channel,
                "campaign_id": pause.campaign_id,
                "rounds": pause.pause_rounds,
                "reason": "write_blocked",
            },
        )
        return
    await log_event(
        "WARNING",
        "neurocomment_channel_paused",
        account_id=account_id,
        extra={
            "channel": channel,
            "rounds": pause.pause_rounds,
            "max_rounds": nc.channel_max_rounds,
            "paused_until": pause.paused_until,
        },
    )


async def clear_write_failures(channel: str) -> None:
    """A comment was delivered: drop ``channel``'s failure window AND its rounds.

    Both, not just the window: sporadic failures across many successes must not
    accumulate to K, and a channel that works again must not carry three old rounds into
    its next bad day. Keyed on a *solved challenge* the reset never fired on a channel
    that issues none, and since gates feed the same counter, isolated per-account gates
    would accumulate with no decay and eventually pause a channel the other accounts post
    to fine.
    """
    _state.reset_write_failures(channel)
    await clear_channel_pause(channel)
