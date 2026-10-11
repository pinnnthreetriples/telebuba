"""Start, Pause, board actions, story media and list imports.

A split sibling of ``masstagging.py``, mounted onto its router with no prefix and no
tags of its own. Statuses are raised as literals in the route bodies because
``tests/test_api_error_contract`` reads them from there.
"""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, File, HTTPException, UploadFile
from fastapi import status as http_status

from api.errors import error_responses
from api.v1._uploads import reject_oversized_upload
from core.config import settings
from schemas.masstagging import (
    MasstaggingBaseRequest,
    MasstaggingMediaRead,
    MasstaggingTargetAction,
    MasstaggingTargetList,
    MasstaggingVersionRequest,
)
from schemas.masstagging_board import MasstaggingBoard
from services import masstagging as mt_service

run_router = APIRouter()

_NOT_FOUND = "campaign not found"


async def _board(campaign_id: str) -> MasstaggingBoard:
    board = await mt_service.load_board(campaign_id)
    if board is None:
        raise HTTPException(status_code=http_status.HTTP_404_NOT_FOUND, detail=_NOT_FOUND)
    return board


async def _known(campaign_id: str) -> None:
    if not await mt_service.campaign_exists(campaign_id):
        raise HTTPException(status_code=http_status.HTTP_404_NOT_FOUND, detail=_NOT_FOUND)


@run_router.post(
    "/campaigns/{campaign_id}/start",
    response_model=MasstaggingBoard,
    operation_id="startMasstaggingCampaign",
    responses=error_responses(404, 409),
)
async def start_campaign(campaign_id: str, body: MasstaggingVersionRequest) -> MasstaggingBoard:
    """Start, continue a paused or stopped campaign, or start a finished one again.

    409 covers every reason it cannot: already running, changed since it was read, no
    account, no list, no media, or no account that may work.
    """
    try:
        found = await mt_service.start_campaign(
            campaign_id, expected_updated_at=body.expected_updated_at
        )
    except mt_service.MasstaggingConflictError as exc:
        raise HTTPException(status_code=http_status.HTTP_409_CONFLICT, detail=exc.code) from exc
    if not found:
        raise HTTPException(status_code=http_status.HTTP_404_NOT_FOUND, detail=_NOT_FOUND)
    return await _board(campaign_id)


@run_router.post(
    "/campaigns/{campaign_id}/stop",
    response_model=MasstaggingBoard,
    operation_id="stopMasstaggingCampaign",
    responses=error_responses(404),
)
async def stop_campaign(campaign_id: str) -> MasstaggingBoard:
    """Pause for real and answer with the board; pausing a paused one is a no-op."""
    if not await mt_service.stop_campaign(campaign_id):
        raise HTTPException(status_code=http_status.HTTP_404_NOT_FOUND, detail=_NOT_FOUND)
    return await _board(campaign_id)


@run_router.post(
    "/campaigns/{campaign_id}/targets/action",
    response_model=MasstaggingBoard,
    operation_id="actOnMasstaggingTarget",
    responses=error_responses(404, 409),
)
async def act_on_target(campaign_id: str, body: MasstaggingTargetAction) -> MasstaggingBoard:
    """Tag now, skip, or put back in the queue one person of the board.

    409 ``masstagging_target_state_changed`` when the person moved on before the click
    landed.
    """
    try:
        found = await mt_service.act_on_target(campaign_id, body)
    except mt_service.MasstaggingConflictError as exc:
        raise HTTPException(status_code=http_status.HTTP_409_CONFLICT, detail=exc.code) from exc
    if not found:
        raise HTTPException(status_code=http_status.HTTP_404_NOT_FOUND, detail=_NOT_FOUND)
    return await _board(campaign_id)


@run_router.post(
    "/campaigns/{campaign_id}/media",
    response_model=MasstaggingMediaRead,
    operation_id="uploadMasstaggingMedia",
    responses=error_responses(400, 404),
)
async def upload_media(
    campaign_id: str, file: Annotated[UploadFile, File()]
) -> MasstaggingMediaRead:
    """Store a photo or a video for the campaign's stories."""
    filename = file.filename or "story.jpg"
    reject_oversized_upload(
        file, max_bytes=mt_service.max_bytes(filename), detail="masstagging_media_too_large"
    )
    await _known(campaign_id)
    content = await file.read()
    try:
        return await mt_service.upload_media(filename, content)
    except mt_service.MasstaggingInvalidError as exc:
        raise HTTPException(status_code=http_status.HTTP_400_BAD_REQUEST, detail=exc.code) from exc


@run_router.post(
    "/campaigns/{campaign_id}/targets/import",
    response_model=MasstaggingTargetList,
    operation_id="importMasstaggingTargets",
    responses=error_responses(400, 404),
)
async def import_targets(
    campaign_id: str, file: Annotated[UploadFile, File()]
) -> MasstaggingTargetList:
    """Read a text or CSV list; the dialog adds what it found to its list."""
    reject_oversized_upload(
        file, max_bytes=settings.masstagging.import_max_bytes, detail="masstagging_import_invalid"
    )
    await _known(campaign_id)
    try:
        return mt_service.import_file(await file.read())
    except mt_service.MasstaggingInvalidError as exc:
        raise HTTPException(status_code=http_status.HTTP_400_BAD_REQUEST, detail=exc.code) from exc


@run_router.post(
    "/campaigns/{campaign_id}/targets/base",
    response_model=MasstaggingTargetList,
    operation_id="copyMasstaggingBase",
    responses=error_responses(400, 404),
)
async def copy_base(campaign_id: str, body: MasstaggingBaseRequest) -> MasstaggingTargetList:
    """A saved parser base as usernames, copied — the campaign never follows the base."""
    await _known(campaign_id)
    try:
        return await mt_service.from_base(body.base_id)
    except mt_service.MasstaggingInvalidError as exc:
        raise HTTPException(status_code=http_status.HTTP_400_BAD_REQUEST, detail=exc.code) from exc
