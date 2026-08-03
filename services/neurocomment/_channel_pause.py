"""The rule for a channel that will not let us write (#147).

K consecutive write failures on a channel end a *round*: it is paused for a flat
``channel_pause_hours``, in which nothing posts there and no account is onboarded to it,
and its round counter goes up. Once the counter reaches ``channel_max_rounds`` the
channel leaves its campaign instead of pausing again — but only once every account that
serves it has actually been tried there; while any has not, the round buys another pause
and the counter keeps climbing. A delivered comment clears both — the channel
demonstrably works.

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
from core.db import (
    bump_channel_pause,
    clear_channel_pause,
    list_campaign_accounts,
    list_channel_readiness,
)
from core.logging import log_event
from services.neurocomment import _state
from services.neurocomment._pins import serving_accounts


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
    extra: dict[str, object] = {
        "channel": channel,
        "rounds": pause.pause_rounds,
        "max_rounds": nc.channel_max_rounds,
        "paused_until": pause.paused_until,
    }
    if pause.pause_rounds >= nc.channel_max_rounds:
        untried = await _untried_serving_accounts(pause.campaign_id, channel)
        if not untried:
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
        # The budget is spent but the verdict is not earned, so the round buys another
        # pause and the counter keeps climbing: the first round that ends with the fleet
        # complete drops the channel. The hold cannot outlive the untried accounts. The
        # gate parks every pair it hits (``ready=False``), so only an onboarding pass can
        # set up the next round at all, and a pass that runs with the pause off writes a
        # row for every serving pair it reaches — the one outcome that leaves none is the
        # rolling-24h join cap, which clears inside a pause window. Meanwhile the channel
        # is parked and posts nothing, so holding costs the fleet one gated post per
        # window, exactly as rounds 1..3 do.
        extra["untried_accounts"] = untried
    await log_event(
        "WARNING",
        "neurocomment_channel_paused",
        account_id=account_id,
        extra=extra,
    )


async def _untried_serving_accounts(campaign_id: str, channel: str) -> int:
    """How many accounts serving ``channel`` have never been tried on it.

    The coverage rule of ``bans._unlink_channel_if_no_account_left`` and its two siblings
    (``_sweep._drop_unapproved_channel``, ``_rejoin._drop_channel_if_nothing_works``),
    resolved through the one shared pin definition so the four cannot drift apart: a
    serving account with NO readiness row was never tried here, not tried and failed.
    Onboarding reaches a fleet slowly — jitter plus the rolling-24h join cap — and this
    rule's own pause turns it away meanwhile, so without the check three gated accounts
    unlinked a channel the campaign's other three had never once opened.

    Their SECOND clause — any still-usable row keeps the channel — is deliberately absent:
    no row here can carry that meaning. ``ready`` says selectable, which every account is
    right up to the moment the gate hits it, and the only proof this channel takes comments
    is a delivered one, which zeroes the rounds through ``clear_write_failures`` anyway.
    Coverage is the whole test. Two reads, and only on the last round of a bad channel.
    """
    links = (await list_campaign_accounts(campaign_id)).links
    serving = serving_accounts(links, channel)
    rows = (await list_channel_readiness(campaign_id, channel, serving)).readiness
    return len(serving) - len(rows)


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
