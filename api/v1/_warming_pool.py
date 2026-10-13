"""«Найти каналы»: the channel-pool search and the operator's approval of its finds (4.5).

Split-sibling of ``warming.py``, mounted onto its router. The search proposes; nothing
it finds is walked until the operator approves it here (decision Q12).
"""

from __future__ import annotations

from fastapi import APIRouter

from schemas._warming_pool import (
    ChannelBatchRequest,
    PoolFillRequest,
    PoolFillResult,
)
from schemas.warming import WarmingChannelList
from services.warming import pool_fill

pool_router = APIRouter()


@pool_router.post(
    "/channels/find",
    response_model=PoolFillResult,
    operation_id="findWarmingChannels",
)
async def find_warming_channels(body: PoolFillRequest) -> PoolFillResult:
    """Search channels for the pool with an account outside warming; propose the fits."""
    return await pool_fill.find_channels(body)


@pool_router.get(
    "/channels/proposals",
    response_model=WarmingChannelList,
    operation_id="listWarmingChannelProposals",
)
async def list_warming_channel_proposals() -> WarmingChannelList:
    return await pool_fill.list_proposals()


@pool_router.post(
    "/channels/proposals/approve",
    response_model=WarmingChannelList,
    operation_id="approveWarmingChannelProposals",
)
async def approve_warming_channel_proposals(body: ChannelBatchRequest) -> WarmingChannelList:
    """Move the ticked proposals into the pool; returns the proposals still waiting."""
    return await pool_fill.approve_proposals(body)


@pool_router.post(
    "/channels/proposals/reject",
    response_model=WarmingChannelList,
    operation_id="rejectWarmingChannelProposals",
)
async def reject_warming_channel_proposals(body: ChannelBatchRequest) -> WarmingChannelList:
    """Drop the ticked proposals; returns the proposals still waiting."""
    return await pool_fill.reject_proposals(body)
