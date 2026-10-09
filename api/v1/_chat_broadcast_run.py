"""Start, Stop, board actions, link resolution, own groups and photo upload.

A split sibling of ``chat_broadcast.py``, mounted onto its router with no prefix and no
tags of its own. Statuses are raised as literals in the route bodies because
``tests/test_api_error_contract`` reads them from there.
"""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, File, HTTPException, Query, UploadFile
from fastapi import status as http_status

from api.errors import error_responses
from api.v1._uploads import reject_oversized_upload
from core.config import settings
from schemas.chat_broadcast import (
    ChatBroadcastMediaRead,
    ChatBroadcastResolveRequest,
    ChatBroadcastTargetAction,
    ChatBroadcastVersionRequest,
)
from schemas.chat_broadcast_board import (
    ChatBroadcastBoard,
    ChatBroadcastOwnChats,
    ChatBroadcastResolved,
)
from services import chat_broadcast as cb_service

run_router = APIRouter()

_NOT_FOUND = "campaign not found"
_MAX_OWN_ACCOUNTS = 200


async def _board(campaign_id: str) -> ChatBroadcastBoard:
    board = await cb_service.load_board(campaign_id)
    if board is None:
        raise HTTPException(status_code=http_status.HTTP_404_NOT_FOUND, detail=_NOT_FOUND)
    return board


@run_router.post(
    "/campaigns/{campaign_id}/start",
    response_model=ChatBroadcastBoard,
    operation_id="startChatBroadcastCampaign",
    responses=error_responses(404, 409),
)
async def start_campaign(campaign_id: str, body: ChatBroadcastVersionRequest) -> ChatBroadcastBoard:
    """Start the campaign, or continue a stopped one from where it was.

    409 covers every reason it cannot: already running, changed since it was read, no
    message, no chat, no account, or every account busy with another feature.
    """
    try:
        found = await cb_service.start_campaign(
            campaign_id, expected_updated_at=body.expected_updated_at
        )
    except cb_service.ChatBroadcastConflictError as exc:
        raise HTTPException(status_code=http_status.HTTP_409_CONFLICT, detail=exc.code) from exc
    if not found:
        raise HTTPException(status_code=http_status.HTTP_404_NOT_FOUND, detail=_NOT_FOUND)
    return await _board(campaign_id)


@run_router.post(
    "/campaigns/{campaign_id}/stop",
    response_model=ChatBroadcastBoard,
    operation_id="stopChatBroadcastCampaign",
    responses=error_responses(404),
)
async def stop_campaign(campaign_id: str) -> ChatBroadcastBoard:
    """Stop for real and answer with the board; stopping a stopped one is a no-op."""
    if not await cb_service.stop_campaign(campaign_id):
        raise HTTPException(status_code=http_status.HTTP_404_NOT_FOUND, detail=_NOT_FOUND)
    return await _board(campaign_id)


@run_router.post(
    "/campaigns/{campaign_id}/targets/action",
    response_model=ChatBroadcastBoard,
    operation_id="actOnChatBroadcastTarget",
    responses=error_responses(400, 404, 409),
)
async def act_on_target(campaign_id: str, body: ChatBroadcastTargetAction) -> ChatBroadcastBoard:
    """Write now, hand to another account, skip, or keep one chat of the board.

    ``keep`` returns a chat skipped for deleted messages; deletions no longer skip it.
    409 ``target_state_changed`` when the chat moved on before the click landed, or
    ``keep`` was asked of a chat not skipped for deletions.
    """
    try:
        found = await cb_service.act_on_target(campaign_id, body)
    except cb_service.ChatBroadcastConflictError as exc:
        raise HTTPException(status_code=http_status.HTTP_409_CONFLICT, detail=exc.code) from exc
    except cb_service.ChatBroadcastInvalidError as exc:
        raise HTTPException(status_code=http_status.HTTP_400_BAD_REQUEST, detail=exc.code) from exc
    if not found:
        raise HTTPException(status_code=http_status.HTTP_404_NOT_FOUND, detail=_NOT_FOUND)
    return await _board(campaign_id)


@run_router.post(
    "/targets/resolve",
    response_model=ChatBroadcastResolved,
    operation_id="resolveChatBroadcastTargets",
)
async def resolve_targets(body: ChatBroadcastResolveRequest) -> ChatBroadcastResolved:
    """Classify pasted links; a folder link is opened with the first account."""
    return await cb_service.resolve(body)


@run_router.get(
    "/own-chats",
    response_model=ChatBroadcastOwnChats,
    operation_id="listChatBroadcastOwnChats",
)
async def own_chats(
    account_ids: Annotated[list[str], Query(max_length=_MAX_OWN_ACCOUNTS)],
) -> ChatBroadcastOwnChats:
    """The groups these accounts are in and may write to."""
    return await cb_service.own_chats(account_ids)


@run_router.post(
    "/media",
    response_model=ChatBroadcastMediaRead,
    operation_id="uploadChatBroadcastPhoto",
    responses=error_responses(400),
)
async def upload_photo(file: Annotated[UploadFile, File()]) -> ChatBroadcastMediaRead:
    """Store a photo for a text message; the text goes out as its caption."""
    reject_oversized_upload(
        file, max_bytes=settings.profile_media.photo_max_bytes, detail="media_too_large"
    )
    content = await file.read()
    try:
        return await cb_service.upload_photo(file.filename or "photo.jpg", content)
    except cb_service.ChatBroadcastInvalidError as exc:
        raise HTTPException(status_code=http_status.HTTP_400_BAD_REQUEST, detail=exc.code) from exc
