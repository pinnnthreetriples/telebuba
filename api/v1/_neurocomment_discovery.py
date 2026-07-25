"""Channel-discovery endpoints — start a search, read the board, adopt candidates.

Split-sibling of ``neurocomment.py`` (same pattern as ``_accounts_channels.py``);
mounted onto the neurocomment router via ``include_router``.

The search is a background run, so the POST answers ``202 Accepted`` with an
outcome status rather than the results. Refusals (another run in flight, no usable
account, that account cooling off, allowance spent) arrive as that status, not as
an error — none of them is a client mistake.
"""

from __future__ import annotations

from fastapi import APIRouter, HTTPException
from fastapi import status as http_status

from schemas.neurocomment_discovery import (
    DiscoveryAdoptRequest,
    DiscoveryAdoptResult,
    DiscoveryBoard,
    DiscoverySearchOutcome,
    DiscoverySearchRequest,
)
from services import neurocomment as nc_service

# No tags: mounted onto the neurocomment router (already tagged "neurocomment").
discovery_router = APIRouter()


@discovery_router.post(
    "/campaigns/{campaign_id}/discovery/search",
    response_model=DiscoverySearchOutcome,
    status_code=http_status.HTTP_202_ACCEPTED,
    operation_id="startCampaignDiscovery",
)
async def start_discovery(
    campaign_id: str,
    body: DiscoverySearchRequest,
) -> DiscoverySearchOutcome:
    return await nc_service.start_discovery(campaign_id, body)


@discovery_router.get(
    "/campaigns/{campaign_id}/discovery",
    response_model=DiscoveryBoard,
    operation_id="getCampaignDiscovery",
)
async def get_discovery(campaign_id: str) -> DiscoveryBoard:
    board = await nc_service.load_discovery(campaign_id)
    if board is None:
        raise HTTPException(status_code=http_status.HTTP_404_NOT_FOUND, detail="campaign not found")
    return board


@discovery_router.post(
    "/campaigns/{campaign_id}/discovery/adopt",
    response_model=DiscoveryAdoptResult,
    operation_id="adoptCampaignDiscovery",
)
async def adopt_discovery(
    campaign_id: str,
    body: DiscoveryAdoptRequest,
) -> DiscoveryAdoptResult:
    result = await nc_service.adopt_candidates(campaign_id, body.channels)
    if result is None:
        raise HTTPException(status_code=http_status.HTTP_404_NOT_FOUND, detail="campaign not found")
    return result
