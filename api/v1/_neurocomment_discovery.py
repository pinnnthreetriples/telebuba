"""Channel-discovery endpoints — expand a topic, start a search, read the board, adopt.

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

from api.errors import error_responses
from schemas.neurocomment_discovery import (
    DiscoveryAdoptRequest,
    DiscoveryAdoptResult,
    DiscoveryBoard,
    DiscoveryKeywordRequest,
    DiscoveryKeywordResult,
    DiscoverySearchOutcome,
    DiscoverySearchRequest,
)
from services import neurocomment as nc_service

# No tags: mounted onto the neurocomment router (already tagged "neurocomment").
discovery_router = APIRouter()
# The campaign-scoped routes answer 404 for an unknown campaign. Declared per route
# rather than once on the router since ``expandDiscoveryKeywords`` joined: it takes
# no campaign and so cannot answer 404, and ``tests/test_api_error_contract.py``
# fails an operation that declares a status it cannot reach.
_CAMPAIGN_ERRORS = error_responses(404)


@discovery_router.post(
    "/campaigns/{campaign_id}/discovery/search",
    response_model=DiscoverySearchOutcome,
    status_code=http_status.HTTP_202_ACCEPTED,
    operation_id="startCampaignDiscovery",
    responses=_CAMPAIGN_ERRORS,
)
async def start_discovery(
    campaign_id: str,
    body: DiscoverySearchRequest,
) -> DiscoverySearchOutcome:
    outcome = await nc_service.start_discovery(campaign_id, body)
    if outcome is None:
        raise HTTPException(status_code=http_status.HTTP_404_NOT_FOUND, detail="campaign not found")
    return outcome


@discovery_router.get(
    "/campaigns/{campaign_id}/discovery",
    response_model=DiscoveryBoard,
    operation_id="getCampaignDiscovery",
    responses=_CAMPAIGN_ERRORS,
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
    responses=_CAMPAIGN_ERRORS,
)
async def adopt_discovery(
    campaign_id: str,
    body: DiscoveryAdoptRequest,
) -> DiscoveryAdoptResult:
    result = await nc_service.adopt_candidates(campaign_id, body.channels)
    if result is None:
        raise HTTPException(status_code=http_status.HTTP_404_NOT_FOUND, detail="campaign not found")
    return result


@discovery_router.post(
    "/discovery/keywords",
    response_model=DiscoveryKeywordResult,
    operation_id="expandDiscoveryKeywords",
)
async def expand_discovery_keywords(body: DiscoveryKeywordRequest) -> DiscoveryKeywordResult:
    """Widen one typed topic into search keywords. Not campaign-scoped, hence no id.

    Never fails on the LLM's behalf: an unusable answer is a 200 carrying a short
    ``error`` code, because the operator's next move (type the keywords by hand) is
    the same either way and a toast-worthy 5xx would only imply the board is broken.
    """
    return await nc_service.expand_discovery_keywords(body)
