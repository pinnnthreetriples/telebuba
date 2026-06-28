"""Neurocomment endpoints — thin routes over ``services.neurocomment``."""

from __future__ import annotations

from fastapi import APIRouter, HTTPException
from fastapi import status as http_status

from schemas.neurocomment import (
    AssignAccountRequest,
    CampaignCreate,
    CampaignList,
    ChannelLinkOutcome,
    LinkChannelRequest,
    NeurocommentBoard,
    NeurocommentCampaign,
    NeurocommentRuntimeStatus,
    StartNeurocommentRequest,
)
from services import neurocomment as nc_service

router = APIRouter(prefix="/neurocomment", tags=["neurocomment"])


@router.get("/campaigns", response_model=CampaignList, operation_id="listCampaigns")
async def list_campaigns() -> CampaignList:
    return await nc_service.list_campaigns()


@router.post("/campaigns", response_model=NeurocommentCampaign, operation_id="createCampaign")
async def create_campaign(body: CampaignCreate) -> NeurocommentCampaign:
    return await nc_service.create_campaign(body)


@router.get(
    "/campaigns/{campaign_id}/board",
    response_model=NeurocommentBoard,
    operation_id="getNeurocommentBoard",
)
async def get_board(campaign_id: str) -> NeurocommentBoard:
    board = await nc_service.load_neurocomment_board(campaign_id)
    if board is None:
        raise HTTPException(status_code=http_status.HTTP_404_NOT_FOUND, detail="campaign not found")
    return board


@router.post(
    "/campaigns/{campaign_id}/channels",
    response_model=ChannelLinkOutcome,
    operation_id="linkCampaignChannel",
)
async def link_channel(campaign_id: str, body: LinkChannelRequest) -> ChannelLinkOutcome:
    return await nc_service.link_channel(campaign_id, body.channel)


@router.post(
    "/campaigns/{campaign_id}/accounts",
    status_code=http_status.HTTP_204_NO_CONTENT,
    operation_id="assignCampaignAccount",
)
async def assign_account(campaign_id: str, body: AssignAccountRequest) -> None:
    await nc_service.assign_account_to_campaign(campaign_id, body.account_id)


@router.get(
    "/runtime",
    response_model=NeurocommentRuntimeStatus,
    operation_id="getNeurocommentRuntime",
)
async def get_runtime() -> NeurocommentRuntimeStatus:
    return await nc_service.neurocomment_runtime_status()


@router.post("/start", response_model=NeurocommentRuntimeStatus, operation_id="startNeurocomment")
async def start(body: StartNeurocommentRequest) -> NeurocommentRuntimeStatus:
    await nc_service.start_neurocomment(body.listener_account_id)
    return await nc_service.neurocomment_runtime_status()


@router.post("/stop", response_model=NeurocommentRuntimeStatus, operation_id="stopNeurocomment")
async def stop() -> NeurocommentRuntimeStatus:
    await nc_service.stop_neurocomment()
    return await nc_service.neurocomment_runtime_status()
