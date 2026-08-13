"""Campaign cleanup after a terminal per-account channel verdict."""

from __future__ import annotations

from core.db import (
    fetch_active_campaign_for_channel,
    list_campaign_accounts,
    list_channel_readiness,
)
from core.logging import log_event
from services.neurocomment._pins import serving_accounts


async def unlink_channel_if_no_account_left(account_id: str, channel: str) -> None:
    """Drop a channel once every account that can serve it has a terminal verdict.

    Readiness is persisted and intentionally replaces live Telegram probes on this hot
    path. Missing rows keep the channel: they mean an account has not been tried yet.
    """
    campaign = await fetch_active_campaign_for_channel(channel)
    if campaign is None:
        return
    links = (await list_campaign_accounts(campaign.campaign_id)).links
    serving = serving_accounts(links, channel)
    rows = (await list_channel_readiness(campaign.campaign_id, channel, serving)).readiness
    if len(rows) != len(serving) or any(
        not (row.banned or row.human_skipped or row.captcha_gave_up) for row in rows
    ):
        return

    # Late import avoids campaigns -> runtime -> bans -> campaigns.
    from services.neurocomment import campaigns as campaigns_service  # noqa: PLC0415

    await campaigns_service.deactivate_channel(campaign.campaign_id, channel)
    await log_event(
        "WARNING",
        "neurocomment_channel_all_accounts_banned",
        account_id=account_id,
        extra={
            "channel": channel,
            "campaign_id": campaign.campaign_id,
            "banned_accounts": sum(1 for row in rows if row.banned),
            "reason": "all_accounts_banned",
        },
    )
