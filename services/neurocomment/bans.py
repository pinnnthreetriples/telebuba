"""Live per-channel ban check for a campaign's channels ("Проверить каналы").

For each channel, probe every serving account's participant state in the linked
discussion group (read-only ``CheckBannedInChannel`` — no message sent) and
aggregate a per-channel verdict. Pin-aware account resolution mirrors
``engine._select_account``; probes are bounded by a semaphore so a burst of
``GetParticipant`` reads can't trip flood limits. A probe fault degrades to
``unknown`` — the check never crashes.
"""

from __future__ import annotations

import asyncio
from typing import TYPE_CHECKING, Literal

from core.config import settings
from core.db import (
    clear_pair_banned,
    fetch_campaign,
    list_campaign_accounts,
    list_campaign_channels,
)
from schemas.neurocomment_bans import ChannelBanCheck, ChannelBanCheckList
from schemas.telegram_actions import BanCheckResult, CheckBannedInChannel
from services.neurocomment import _seams

if TYPE_CHECKING:
    from schemas.neurocomment import CampaignAccountLink

_ChannelStatus = Literal["ok", "banned", "unknown"]


def _aggregate(states: list[str]) -> _ChannelStatus:
    """A channel is ok if any account can comment, banned if all are blocked."""
    if any(state == "can_send" for state in states):
        return "ok"
    if any(state in ("restricted", "not_member") for state in states):
        return "banned"
    return "unknown"


async def check_campaign_channel_bans(campaign_id: str) -> ChannelBanCheckList | None:
    """Probe each campaign channel for bans, or ``None`` if the campaign is gone."""
    campaign = await fetch_campaign(campaign_id)
    if campaign is None:
        return None

    account_links = (await list_campaign_accounts(campaign_id)).links
    channels = [link.channel for link in (await list_campaign_channels(campaign_id)).links]
    semaphore = asyncio.Semaphore(settings.neurocomment.ban_check_concurrency)

    async def _probe(account_id: str, channel: str) -> str:
        async with semaphore:
            try:
                result = await _seams.execute_read(
                    account_id, CheckBannedInChannel(channel=channel)
                )
            except Exception:  # noqa: BLE001 - a probe fault degrades to "unknown".
                return "unknown"
        return result.state if isinstance(result, BanCheckResult) else "unknown"

    async def _check_channel(channel: str) -> ChannelBanCheck:
        # Pin rule: unpinned accounts serve every channel; pinned only their own.
        serving = _serving_accounts(account_links, channel)
        if not serving:
            return ChannelBanCheck(channel=channel, status="unknown")
        states = await asyncio.gather(*(_probe(acc, channel) for acc in serving))
        # Recovery (#30): a live can_send verdict is proof the account may write again,
        # so lift any sticky auto-ban on that pair — this button is the un-ban path.
        for account_id, state in zip(serving, states, strict=True):
            if state == "can_send":
                await clear_pair_banned(account_id, channel)
        return ChannelBanCheck(channel=channel, status=_aggregate(list(states)))

    items = await asyncio.gather(*(_check_channel(channel) for channel in channels))
    return ChannelBanCheckList(items=list(items))


def _serving_accounts(links: list[CampaignAccountLink], channel: str) -> list[str]:
    return [link.account_id for link in links if not link.channels or channel in link.channels]
