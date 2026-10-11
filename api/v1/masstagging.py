"""Masstagging endpoints — thin routes over ``services.masstagging``.

Campaigns, the whole-dialog settings save, the board's «Темп» and the board are here;
running a campaign, the board's per-target actions, story media and list imports are
in ``_masstagging_run``, mounted onto this router. Refusals answer 400 or 409 with the
domain's stable code as the envelope ``message``.
"""

from __future__ import annotations

from fastapi import APIRouter, HTTPException
from fastapi import status as http_status

from api.errors import error_responses
from api.v1._masstagging_run import run_router
from schemas.masstagging import (
    MasstaggingCampaign,
    MasstaggingCampaigns,
    MasstaggingCreate,
    MasstaggingPace,
    MasstaggingSettingsRead,
    MasstaggingSettingsUpdate,
)
from schemas.masstagging_board import MasstaggingBoard
from services import masstagging as mt_service

router = APIRouter(prefix="/masstagging", tags=["masstagging"])

_NOT_FOUND = "campaign not found"


@router.get(
    "/campaigns",
    response_model=MasstaggingCampaigns,
    operation_id="listMasstaggingCampaigns",
)
async def list_campaigns() -> MasstaggingCampaigns:
    return await mt_service.list_campaigns()


@router.post(
    "/campaigns",
    response_model=MasstaggingCampaign,
    operation_id="createMasstaggingCampaign",
)
async def create_campaign(body: MasstaggingCreate) -> MasstaggingCampaign:
    return await mt_service.create_campaign(body.name)


@router.delete(
    "/campaigns/{campaign_id}",
    status_code=http_status.HTTP_204_NO_CONTENT,
    operation_id="deleteMasstaggingCampaign",
    responses=error_responses(404, 409),
)
async def delete_campaign(campaign_id: str) -> None:
    """Delete a campaign with its list and history; refused while it runs."""
    try:
        deleted = await mt_service.delete_campaign(campaign_id)
    except mt_service.MasstaggingConflictError as exc:
        raise HTTPException(status_code=http_status.HTTP_409_CONFLICT, detail=exc.code) from exc
    if not deleted:
        raise HTTPException(status_code=http_status.HTTP_404_NOT_FOUND, detail=_NOT_FOUND)


@router.get(
    "/campaigns/{campaign_id}/settings",
    response_model=MasstaggingSettingsRead,
    operation_id="getMasstaggingSettings",
    responses=error_responses(404),
)
async def get_settings(campaign_id: str) -> MasstaggingSettingsRead:
    settings = await mt_service.load_settings(campaign_id)
    if settings is None:
        raise HTTPException(status_code=http_status.HTTP_404_NOT_FOUND, detail=_NOT_FOUND)
    return settings


@router.put(
    "/campaigns/{campaign_id}/settings",
    response_model=MasstaggingSettingsRead,
    operation_id="saveMasstaggingSettings",
    responses=error_responses(400, 404, 409),
)
async def save_settings(
    campaign_id: str, body: MasstaggingSettingsUpdate
) -> MasstaggingSettingsRead:
    """Save name, accounts and settings at once, only on the version the dialog read.

    409 ``campaign_running`` while a run is attached, ``campaign_changed`` when the
    campaign moved since the dialog read it.
    """
    try:
        saved = await mt_service.save_settings(campaign_id, body)
    except mt_service.MasstaggingConflictError as exc:
        raise HTTPException(status_code=http_status.HTTP_409_CONFLICT, detail=exc.code) from exc
    except mt_service.MasstaggingInvalidError as exc:
        raise HTTPException(status_code=http_status.HTTP_400_BAD_REQUEST, detail=exc.code) from exc
    if saved is None:
        raise HTTPException(status_code=http_status.HTTP_404_NOT_FOUND, detail=_NOT_FOUND)
    return saved


@router.patch(
    "/campaigns/{campaign_id}/pace",
    response_model=MasstaggingSettingsRead,
    operation_id="saveMasstaggingPace",
    responses=error_responses(404, 409),
)
async def save_pace(campaign_id: str, body: MasstaggingPace) -> MasstaggingSettingsRead:
    """Save «Темп» only — accepted while the campaign runs; the run reads it live."""
    try:
        saved = await mt_service.save_pace(campaign_id, body)
    except mt_service.MasstaggingConflictError as exc:
        raise HTTPException(status_code=http_status.HTTP_409_CONFLICT, detail=exc.code) from exc
    if saved is None:
        raise HTTPException(status_code=http_status.HTTP_404_NOT_FOUND, detail=_NOT_FOUND)
    return saved


@router.get(
    "/campaigns/{campaign_id}/board",
    response_model=MasstaggingBoard,
    operation_id="getMasstaggingBoard",
    responses=error_responses(404),
)
async def get_board(campaign_id: str) -> MasstaggingBoard:
    """The pipeline's numbers, the accounts and the board by person with its history."""
    board = await mt_service.load_board(campaign_id)
    if board is None:
        raise HTTPException(status_code=http_status.HTTP_404_NOT_FOUND, detail=_NOT_FOUND)
    return board


router.include_router(run_router)
