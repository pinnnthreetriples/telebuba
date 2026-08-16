"""Neuroshilling endpoints — thin routes over ``services.neuroshilling``.

Five operations, not one per field: the board is a single composite read, and
targets plus the account roster ride inside ``updateNeuroshillingCampaign``
because the page edits them as one form and splitting the write would leave
windows where a roster points at something the same save removed.

Refusals answer 400 or 409 with the domain's stable code as the envelope
``message``; 502 is deliberately absent because ``api.errors`` has no description
for it — a provider failure is 503 here.
"""

from __future__ import annotations

from fastapi import APIRouter, HTTPException
from fastapi import status as http_status

from api.errors import error_responses
from api.v1._neuroshilling_run import run_router
from api.v1._neuroshilling_scenario import scenario_router
from schemas.neuroshilling import (
    NeuroshillingBoard,
    NeuroshillingCampaign,
    NeuroshillingCampaignCreate,
    NeuroshillingCampaignList,
    NeuroshillingCampaignUpdate,
)
from services import neuroshilling as ns_service

router = APIRouter(prefix="/neuroshilling", tags=["neuroshilling"])

_NOT_FOUND = "campaign not found"


@router.get(
    "/campaigns",
    response_model=NeuroshillingCampaignList,
    operation_id="listNeuroshillingCampaigns",
)
async def list_campaigns() -> NeuroshillingCampaignList:
    return await ns_service.list_campaigns()


@router.post(
    "/campaigns",
    response_model=NeuroshillingCampaign,
    operation_id="createNeuroshillingCampaign",
)
async def create_campaign(body: NeuroshillingCampaignCreate) -> NeuroshillingCampaign:
    return await ns_service.create_campaign(body)


@router.put(
    "/campaigns/{campaign_id}",
    response_model=NeuroshillingCampaign,
    operation_id="updateNeuroshillingCampaign",
    responses=error_responses(400, 404, 409),
)
async def update_campaign(
    campaign_id: str,
    body: NeuroshillingCampaignUpdate,
) -> NeuroshillingCampaign:
    """Save the whole edited form: settings, targets and the account roster."""
    try:
        campaign = await ns_service.update_campaign(campaign_id, body)
    except ns_service.NeuroshillingConflictError as exc:
        raise HTTPException(status_code=http_status.HTTP_409_CONFLICT, detail=exc.code) from exc
    except ns_service.NeuroshillingInvalidError as exc:
        raise HTTPException(status_code=http_status.HTTP_400_BAD_REQUEST, detail=exc.code) from exc
    if campaign is None:
        raise HTTPException(status_code=http_status.HTTP_404_NOT_FOUND, detail=_NOT_FOUND)
    return campaign


@router.delete(
    "/campaigns/{campaign_id}",
    status_code=http_status.HTTP_204_NO_CONTENT,
    operation_id="deleteNeuroshillingCampaign",
    responses=error_responses(404, 409),
)
async def delete_campaign(campaign_id: str) -> None:
    """Delete a campaign and everything hanging off it. A live run refuses (409)."""
    try:
        deleted = await ns_service.delete_campaign(campaign_id)
    except ns_service.NeuroshillingConflictError as exc:
        raise HTTPException(status_code=http_status.HTTP_409_CONFLICT, detail=exc.code) from exc
    if not deleted:
        raise HTTPException(status_code=http_status.HTTP_404_NOT_FOUND, detail=_NOT_FOUND)


@router.get(
    "/campaigns/{campaign_id}/board",
    response_model=NeuroshillingBoard,
    operation_id="getNeuroshillingBoard",
    responses=error_responses(404),
)
async def get_board(campaign_id: str) -> NeuroshillingBoard:
    """The whole page in one read: campaign, roster, account pool, targets, run."""
    board = await ns_service.load_board(campaign_id)
    if board is None:
        raise HTTPException(status_code=http_status.HTTP_404_NOT_FOUND, detail=_NOT_FOUND)
    return board


# Mounted last so the campaign paths keep their place in the generated document.
router.include_router(scenario_router)
router.include_router(run_router)
