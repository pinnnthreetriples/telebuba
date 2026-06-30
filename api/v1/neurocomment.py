"""Neurocomment endpoints — thin routes over ``services.neurocomment``."""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, HTTPException, Query
from fastapi import status as http_status

from schemas.challenge import ChallengeRowList
from schemas.neurocomment import (
    AccountChannelOnboarding,
    AssignAccountRequest,
    CampaignCreate,
    CampaignList,
    ChannelLinkOutcome,
    LinkChannelRequest,
    NeurocommentBoard,
    NeurocommentCampaign,
    NeurocommentRuntimeStatus,
    NeurocommentSettings,
    NeurocommentSettingsUpdate,
    RetryPairRequest,
    SolverToggleRequest,
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


@router.post(
    "/campaigns/{campaign_id}/solver",
    status_code=http_status.HTTP_204_NO_CONTENT,
    operation_id="setCampaignSolver",
)
async def set_campaign_solver(campaign_id: str, body: SolverToggleRequest) -> None:
    """Turn the campaign's challenge (captcha) solver on/off."""
    await nc_service.set_solver_enabled(campaign_id, body.enabled)


@router.post(
    "/retry",
    response_model=AccountChannelOnboarding,
    operation_id="retryChallenge",
)
async def retry_challenge(body: RetryPairRequest) -> AccountChannelOnboarding:
    """Operator retry of one challenged (account, channel) pair (the captcha «Решить»).

    Re-onboards the pair (re-running the solver) — account+channel scoped, so it
    is campaign-agnostic.
    """
    return await nc_service.retry_pair(body.account_id, body.channel)


@router.get(
    "/campaigns/{campaign_id}/challenges",
    response_model=ChallengeRowList,
    operation_id="listCampaignChallenges",
)
async def list_campaign_challenges(
    campaign_id: str,
    limit: Annotated[int, Query(ge=1, le=100)] = 20,
) -> ChallengeRowList:
    """Recent unsolved bot-challenges across the campaign's channels (captcha queue)."""
    return await nc_service.list_campaign_challenges(campaign_id, limit)


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


@router.get(
    "/settings",
    response_model=NeurocommentSettings,
    operation_id="getNeurocommentSettings",
)
async def get_settings() -> NeurocommentSettings:
    return await nc_service.load_neurocomment_settings()


@router.put(
    "/settings",
    response_model=NeurocommentSettings,
    operation_id="updateNeurocommentSettings",
)
async def update_settings(body: NeurocommentSettingsUpdate) -> NeurocommentSettings:
    return await nc_service.save_neurocomment_settings(body)
