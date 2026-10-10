"""Chat-broadcast endpoints — thin routes over ``services.chat_broadcast``.

Campaigns, the whole-dialog settings save, the board's pauses, and the board are here;
running a campaign, the board's per-chat actions, link resolution and photo upload are
in ``_chat_broadcast_run``, the saved chat lists in ``_chat_broadcast_collections``; both
are mounted onto this router. Refusals answer 400 or 409 with
the domain's stable code as the envelope ``message``.
"""

from __future__ import annotations

from fastapi import APIRouter, HTTPException
from fastapi import status as http_status

from api.errors import error_responses
from api.v1._chat_broadcast_collections import collections_router
from api.v1._chat_broadcast_run import run_router
from schemas.chat_broadcast import (
    ChatBroadcastCampaign,
    ChatBroadcastCampaigns,
    ChatBroadcastCreate,
    ChatBroadcastPace,
    ChatBroadcastSettingsRead,
    ChatBroadcastSettingsUpdate,
)
from schemas.chat_broadcast_board import ChatBroadcastBoard
from services import chat_broadcast as cb_service

router = APIRouter(prefix="/chat-broadcast", tags=["chat_broadcast"])

_NOT_FOUND = "campaign not found"


@router.get(
    "/campaigns",
    response_model=ChatBroadcastCampaigns,
    operation_id="listChatBroadcastCampaigns",
)
async def list_campaigns() -> ChatBroadcastCampaigns:
    return await cb_service.list_campaigns()


@router.post(
    "/campaigns",
    response_model=ChatBroadcastCampaign,
    operation_id="createChatBroadcastCampaign",
)
async def create_campaign(body: ChatBroadcastCreate) -> ChatBroadcastCampaign:
    return await cb_service.create_campaign(body.name)


@router.delete(
    "/campaigns/{campaign_id}",
    status_code=http_status.HTTP_204_NO_CONTENT,
    operation_id="deleteChatBroadcastCampaign",
    responses=error_responses(404, 409),
)
async def delete_campaign(campaign_id: str) -> None:
    """Delete a campaign with its chats and history; refused while it runs."""
    try:
        deleted = await cb_service.delete_campaign(campaign_id)
    except cb_service.ChatBroadcastConflictError as exc:
        raise HTTPException(status_code=http_status.HTTP_409_CONFLICT, detail=exc.code) from exc
    if not deleted:
        raise HTTPException(status_code=http_status.HTTP_404_NOT_FOUND, detail=_NOT_FOUND)


@router.get(
    "/campaigns/{campaign_id}/settings",
    response_model=ChatBroadcastSettingsRead,
    operation_id="getChatBroadcastSettings",
    responses=error_responses(404),
)
async def get_settings(campaign_id: str) -> ChatBroadcastSettingsRead:
    settings = await cb_service.load_settings(campaign_id)
    if settings is None:
        raise HTTPException(status_code=http_status.HTTP_404_NOT_FOUND, detail=_NOT_FOUND)
    return settings


@router.put(
    "/campaigns/{campaign_id}/settings",
    response_model=ChatBroadcastSettingsRead,
    operation_id="saveChatBroadcastSettings",
    responses=error_responses(400, 404, 409),
)
async def save_settings(
    campaign_id: str, body: ChatBroadcastSettingsUpdate
) -> ChatBroadcastSettingsRead:
    """Save name, accounts and settings at once, only on the version the dialog read."""
    try:
        saved = await cb_service.save_settings(campaign_id, body)
    except cb_service.ChatBroadcastConflictError as exc:
        raise HTTPException(status_code=http_status.HTTP_409_CONFLICT, detail=exc.code) from exc
    except cb_service.ChatBroadcastInvalidError as exc:
        raise HTTPException(status_code=http_status.HTTP_400_BAD_REQUEST, detail=exc.code) from exc
    if saved is None:
        raise HTTPException(status_code=http_status.HTTP_404_NOT_FOUND, detail=_NOT_FOUND)
    return saved


@router.put(
    "/campaigns/{campaign_id}/pace",
    response_model=ChatBroadcastSettingsRead,
    operation_id="saveChatBroadcastPace",
    responses=error_responses(404, 409),
)
async def save_pace(campaign_id: str, body: ChatBroadcastPace) -> ChatBroadcastSettingsRead:
    """Save the pauses only — accepted while the campaign runs; the run reads them live.

    409 ``campaign_changed`` when the campaign moved between the read and the write.
    """
    try:
        saved = await cb_service.save_pace(campaign_id, body)
    except cb_service.ChatBroadcastConflictError as exc:
        raise HTTPException(status_code=http_status.HTTP_409_CONFLICT, detail=exc.code) from exc
    if saved is None:
        raise HTTPException(status_code=http_status.HTTP_404_NOT_FOUND, detail=_NOT_FOUND)
    return saved


@router.get(
    "/campaigns/{campaign_id}/board",
    response_model=ChatBroadcastBoard,
    operation_id="getChatBroadcastBoard",
    responses=error_responses(404),
)
async def get_board(campaign_id: str) -> ChatBroadcastBoard:
    """The pipeline, the board by chat with its history, and the accounts."""
    board = await cb_service.load_board(campaign_id)
    if board is None:
        raise HTTPException(status_code=http_status.HTTP_404_NOT_FOUND, detail=_NOT_FOUND)
    return board


router.include_router(run_router)
router.include_router(collections_router)
