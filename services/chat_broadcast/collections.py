"""Saved chat lists («Категории чатов»): reusable sets of links for the chat broadcast.

A list is only a store of links. The settings dialog copies one into a campaign, where
the campaign's own save validates it again; nothing here touches a campaign. Links are
normalised exactly as a campaign's are (``campaigns.valid_targets``), so a list always
fits into one.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from core.logging import log_event
from core.repositories import chat_broadcast as repository
from schemas.chat_broadcast_collections import ChatCollection, ChatCollections
from services.chat_broadcast._errors import COLLECTION_NAME_TAKEN, ChatBroadcastConflictError
from services.chat_broadcast.campaigns import valid_targets

if TYPE_CHECKING:
    from schemas.chat_broadcast_collections import ChatCollectionWrite


async def list_collections() -> ChatCollections:
    return ChatCollections(items=await repository.list_collections())


async def create_collection(body: ChatCollectionWrite) -> ChatCollection:
    """Save a new list; 409 ``collection_name_taken`` when the name is in use."""
    saved = await repository.create_collection(body.name.strip(), valid_targets(body.targets))
    return await _landed(saved, "chat_broadcast_collection_created")


async def replace_collection(collection_id: str, body: ChatCollectionWrite) -> ChatCollection | None:
    """Rename and re-fill a list at once; ``None`` when it does not exist."""
    saved = await repository.replace_collection(
        collection_id, body.name.strip(), valid_targets(body.targets)
    )
    if saved == "not_found":
        return None
    return await _landed(saved, "chat_broadcast_collection_saved")


async def delete_collection(collection_id: str) -> bool:
    deleted = await repository.delete_collection(collection_id)
    if deleted:
        await log_event(
            "INFO", "chat_broadcast_collection_deleted", extra={"collection_id": collection_id}
        )
    return deleted


async def _landed(saved: ChatCollection | repository.CollectionMiss, event: str) -> ChatCollection:
    if not isinstance(saved, ChatCollection):
        raise ChatBroadcastConflictError(COLLECTION_NAME_TAKEN)
    await log_event(
        "INFO",
        event,
        extra={"collection_id": saved.collection_id, "targets": len(saved.targets)},
    )
    return saved
