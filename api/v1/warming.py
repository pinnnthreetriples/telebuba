"""Warming endpoints — thin routes over ``services.warming``."""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, HTTPException, Query
from fastapi import status as http_status

from api.errors import error_responses
from core.config import settings
from schemas.dialogues import DialogueFeed
from schemas.warming import (
    AddChannelsRequest,
    PromoteRequest,
    RemoveChannelRequest,
    StartWarmingRequest,
    StopWarmingRequest,
    WarmedAccountList,
    WarmingAccountState,
    WarmingBoardState,
    WarmingChannelList,
    WarmingSettings,
    WarmingSettingsUpdate,
)
from services import dialogues as dialogues_service
from services import warming as warming_service

router = APIRouter(prefix="/warming", tags=["warming"])


@router.get("/board", response_model=WarmingBoardState, operation_id="getWarmingBoard")
async def get_warming_board() -> WarmingBoardState:
    return await warming_service.load_board()


@router.get("/warmed", response_model=WarmedAccountList, operation_id="listWarmedAccounts")
async def get_warmed_accounts() -> WarmedAccountList:
    """Operator-graduated accounts (the warming page's "Прогретые аккаунты" card)."""
    return await warming_service.list_warmed_accounts(settings.neurocomment.warmed_min_days)


@router.post(
    "/promote",
    response_model=WarmingAccountState,
    operation_id="promoteToNeurocomment",
    responses=error_responses(409),
)
async def promote_account(body: PromoteRequest) -> WarmingAccountState:
    """Graduate an account: stop warming + flag it for the neurocomment pool."""
    try:
        return await warming_service.promote_to_neurocomment(body.account_id)
    except warming_service.WarmingTaskNotQuiescentError as exc:
        raise HTTPException(
            status_code=http_status.HTTP_409_CONFLICT,
            detail="warming task is still stopping",
        ) from exc


@router.post(
    "/handoff",
    response_model=WarmingAccountState,
    operation_id="handoffToNeurocomment",
    responses=error_responses(400, 409),
)
async def handoff_account(body: PromoteRequest) -> WarmingAccountState:
    """Second stage: move a warmed-card account into the neurocomment idle pool."""
    try:
        return await warming_service.handoff_to_neurocomment(body.account_id)
    except warming_service.WarmingTaskNotQuiescentError as exc:
        raise HTTPException(
            status_code=http_status.HTTP_409_CONFLICT,
            detail="warming task is still stopping",
        ) from exc
    except ValueError as exc:
        # Not graduated / unknown account — a stale client racing an un-promote.
        raise HTTPException(status_code=http_status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc


@router.post(
    "/unpromote",
    response_model=WarmingAccountState,
    operation_id="unpromoteFromNeurocomment",
)
async def unpromote_account(body: PromoteRequest) -> WarmingAccountState:
    """Reverse a graduation: clear the promotion flag (the warmed card's «вернуть»)."""
    return await warming_service.unmark_neurocomment(body.account_id)


@router.post(
    "/start",
    response_model=WarmingAccountState,
    operation_id="startWarming",
    responses=error_responses(400, 404, 409),
)
async def start_warming(body: StartWarmingRequest) -> WarmingAccountState:
    try:
        return await warming_service.start_warming(body)
    except warming_service.UnknownAccountError as exc:
        raise HTTPException(status_code=http_status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc
    except warming_service.WarmingTaskNotQuiescentError as exc:
        # Same 409 as promote/handoff: the previous coroutine has not unwound yet, which
        # is a transient lifecycle conflict — not one of the readiness reasons the 400
        # branch below feeds to the "account is not ready" list.
        raise HTTPException(
            status_code=http_status.HTTP_409_CONFLICT,
            detail="warming task is still stopping",
        ) from exc
    except warming_service.WarmingNotReadyError as exc:
        raise HTTPException(status_code=http_status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc
    except warming_service.AccountIsListenerError as exc:
        raise HTTPException(
            status_code=http_status.HTTP_409_CONFLICT,
            detail="account is the neurocomment listener",
        ) from exc
    except warming_service.AccountUnavailableError as exc:
        # Same 409 family as the listener conflict — a discovery run is reading with this
        # account, or Telegram is rate-limiting it. The detail is the service's stable
        # code, reported verbatim so the SPA can translate it.
        raise HTTPException(
            status_code=http_status.HTTP_409_CONFLICT,
            detail=exc.code,
        ) from exc


@router.post(
    "/stop",
    response_model=WarmingAccountState,
    operation_id="stopWarming",
    responses=error_responses(404),
)
async def stop_warming(body: StopWarmingRequest) -> WarmingAccountState:
    try:
        return await warming_service.stop_warming(body)
    except warming_service.UnknownAccountError as exc:
        raise HTTPException(status_code=http_status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc


@router.get("/channels", response_model=WarmingChannelList, operation_id="listWarmingChannels")
async def list_warming_channels() -> WarmingChannelList:
    return await warming_service.list_channels()


@router.post(
    "/channels",
    response_model=WarmingChannelList,
    operation_id="addWarmingChannels",
    responses=error_responses(400),
)
async def add_warming_channels(body: AddChannelsRequest) -> WarmingChannelList:
    try:
        return await warming_service.add_channels(body)
    except ValueError as exc:
        raise HTTPException(status_code=http_status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc


@router.post(
    "/channels/remove",
    response_model=WarmingChannelList,
    operation_id="removeWarmingChannel",
)
async def remove_warming_channel(body: RemoveChannelRequest) -> WarmingChannelList:
    return await warming_service.remove_channel(body)


@router.get("/settings", response_model=WarmingSettings, operation_id="getWarmingSettings")
async def get_warming_settings() -> WarmingSettings:
    return await warming_service.load_settings()


@router.put("/settings", response_model=WarmingSettings, operation_id="updateWarmingSettings")
async def update_warming_settings(body: WarmingSettingsUpdate) -> WarmingSettings:
    return await warming_service.save_settings(body)


@router.get("/dialogues", response_model=DialogueFeed, operation_id="listWarmingDialogues")
async def list_warming_dialogues(
    limit: Annotated[int, Query(ge=1, le=100)] = 30,
) -> DialogueFeed:
    """Recent inter-account warming messages, newest first, for the live feed."""
    return await dialogues_service.load_dialogue_overview(recent_limit=limit)
