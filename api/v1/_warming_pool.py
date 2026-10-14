"""«Найти каналы»: the channel-pool search (4.5 and its follow-up).

Split-sibling of ``warming.py``, mounted onto its router. Everything the search finds
that passes the filters goes straight into the pool; the operator removes what they do
not want through the ordinary channel list. A busy account is only taken when the
request carries the operator's consent (``pull_account_id``).
"""

from __future__ import annotations

from fastapi import APIRouter

from schemas._warming_pool import PoolFillRequest, PoolFillResult
from services.warming import pool_fill

pool_router = APIRouter()


@pool_router.post(
    "/channels/find",
    response_model=PoolFillResult,
    operation_id="findWarmingChannels",
)
async def find_warming_channels(body: PoolFillRequest) -> PoolFillResult:
    """Search channels with an account outside warming; the fits join the pool at once."""
    return await pool_fill.find_channels(body)
