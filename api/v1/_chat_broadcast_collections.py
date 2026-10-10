"""Saved chat lists («Категории чатов») of the chat broadcast.

A split sibling of ``chat_broadcast.py``, mounted onto its router with no prefix and no
tags of its own. A list is copied into a campaign by the settings dialog; these routes
only keep the lists.
"""

from __future__ import annotations

from fastapi import APIRouter, HTTPException
from fastapi import status as http_status

from api.errors import error_responses
from schemas.chat_broadcast_collections import (
    ChatCollection,
    ChatCollections,
    ChatCollectionWrite,
)
from services import chat_broadcast as cb_service

collections_router = APIRouter()

_NOT_FOUND = "collection not found"


@collections_router.get(
    "/collections",
    response_model=ChatCollections,
    operation_id="listChatCollections",
)
async def list_collections() -> ChatCollections:
    return await cb_service.list_collections()


@collections_router.post(
    "/collections",
    response_model=ChatCollection,
    operation_id="createChatCollection",
    responses=error_responses(400, 409),
)
async def create_collection(body: ChatCollectionWrite) -> ChatCollection:
    """Save a named list of chats; 409 ``collection_name_taken`` for a name in use."""
    try:
        return await cb_service.create_collection(body)
    except cb_service.ChatBroadcastConflictError as exc:
        raise HTTPException(status_code=http_status.HTTP_409_CONFLICT, detail=exc.code) from exc
    except cb_service.ChatBroadcastInvalidError as exc:
        raise HTTPException(status_code=http_status.HTTP_400_BAD_REQUEST, detail=exc.code) from exc


@collections_router.put(
    "/collections/{collection_id}",
    response_model=ChatCollection,
    operation_id="saveChatCollection",
    responses=error_responses(400, 404, 409),
)
async def save_collection(collection_id: str, body: ChatCollectionWrite) -> ChatCollection:
    """Replace a list's name and chats at once."""
    try:
        saved = await cb_service.replace_collection(collection_id, body)
    except cb_service.ChatBroadcastConflictError as exc:
        raise HTTPException(status_code=http_status.HTTP_409_CONFLICT, detail=exc.code) from exc
    except cb_service.ChatBroadcastInvalidError as exc:
        raise HTTPException(status_code=http_status.HTTP_400_BAD_REQUEST, detail=exc.code) from exc
    if saved is None:
        raise HTTPException(status_code=http_status.HTTP_404_NOT_FOUND, detail=_NOT_FOUND)
    return saved


@collections_router.delete(
    "/collections/{collection_id}",
    status_code=http_status.HTTP_204_NO_CONTENT,
    operation_id="deleteChatCollection",
    responses=error_responses(404),
)
async def delete_collection(collection_id: str) -> None:
    """Delete a list; campaigns that took chats from it keep them."""
    if not await cb_service.delete_collection(collection_id):
        raise HTTPException(status_code=http_status.HTTP_404_NOT_FOUND, detail=_NOT_FOUND)
