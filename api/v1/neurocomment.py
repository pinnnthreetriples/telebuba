"""Neurocomment endpoints — thin routes over ``services.neurocomment``."""

from __future__ import annotations

from contextlib import contextmanager
from typing import TYPE_CHECKING, Annotated

from fastapi import APIRouter, HTTPException, Query
from fastapi import status as http_status

from api.errors import error_responses
from api.v1._neurocomment_discovery import discovery_router
from schemas.api import Page
from schemas.challenge import ChallengeOutcomeCounts, ChallengeRowList
from schemas.neurocomment import (
    LISTENER_BUSY_NEUROSHILLING_CODE,
    AssignAccountRequest,
    CampaignCreate,
    CampaignList,
    ChannelLinkOutcome,
    CommentRecord,
    LinkChannelRequest,
    NeurocommentCampaign,
    NeurocommentRuntimeStatus,
    NeurocommentSettings,
    NeurocommentSettingsUpdate,
    RetryPairRequest,
    SetAccountChannelRequest,
    SetCampaignStatusRequest,
    SolverToggleRequest,
    StartNeurocommentRequest,
    UpdatePromptRequest,
)
from schemas.neurocomment_bans import ChannelBanCheckList
from schemas.neurocomment_board import NeurocommentBoard
from schemas.neurocomment_discovery import DISCOVERY_BUSY_CODE
from schemas.neurocomment_limits import AccountLimitsUpdate, AccountLimitsView
from services import neurocomment as nc_service

if TYPE_CHECKING:
    from collections.abc import Iterator

router = APIRouter(prefix="/neurocomment", tags=["neurocomment"])
# Channel discovery lives in a sibling module (file-size cap); mounted here so its
# routes share this router's prefix, tag and auth dependency.
router.include_router(discovery_router)


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
    responses=error_responses(404),
)
async def get_board(campaign_id: str) -> NeurocommentBoard:
    board = await nc_service.load_neurocomment_board(campaign_id)
    if board is None:
        raise HTTPException(status_code=http_status.HTTP_404_NOT_FOUND, detail="campaign not found")
    return board


@router.post(
    "/campaigns/{campaign_id}/channel-bans",
    response_model=ChannelBanCheckList,
    operation_id="checkCampaignChannelBans",
    responses=error_responses(404),
)
async def check_channel_bans(campaign_id: str) -> ChannelBanCheckList:
    """Live-probe each campaign channel for account bans (the "Проверить каналы" button)."""
    result = await nc_service.check_campaign_channel_bans(campaign_id)
    if result is None:
        raise HTTPException(status_code=http_status.HTTP_404_NOT_FOUND, detail="campaign not found")
    return result


@router.get(
    "/campaigns/{campaign_id}/comments",
    response_model=Page[CommentRecord],
    operation_id="listNeurocommentComments",
    responses=error_responses(400),
)
async def list_comments(
    campaign_id: str,
    cursor: str | None = None,
    limit: Annotated[int, Query(ge=1, le=200)] = 50,
) -> Page[CommentRecord]:
    """One cursor page of a campaign's posted comments (newest first) — the history modal."""
    try:
        return await nc_service.list_comments_page(campaign_id, cursor, limit)
    except nc_service.InvalidCursorError as exc:
        raise HTTPException(
            status_code=http_status.HTTP_400_BAD_REQUEST,
            detail="invalid pagination cursor",
        ) from exc


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
    "/campaigns/{campaign_id}/accounts/remove",
    status_code=http_status.HTTP_204_NO_CONTENT,
    operation_id="removeCampaignAccount",
)
async def remove_account(campaign_id: str, body: AssignAccountRequest) -> None:
    await nc_service.remove_account_from_campaign(campaign_id, body.account_id)


@router.post(
    "/campaigns/{campaign_id}/accounts/{account_id}/channel",
    response_model=NeurocommentBoard,
    operation_id="setCampaignAccountChannel",
    responses=error_responses(400, 404),
)
async def set_account_channel(
    campaign_id: str,
    account_id: str,
    body: SetAccountChannelRequest,
) -> NeurocommentBoard:
    """Set a campaign account's channel subset (empty ``channels`` = all channels).

    An account with a non-empty subset comments only on those channels; an empty
    subset serves all campaign channels. Returns the refreshed board so the SPA
    re-renders the card.
    """
    try:
        board = await nc_service.set_account_channels(campaign_id, account_id, body.channels)
    except nc_service.ChannelNotInCampaignError as exc:
        raise HTTPException(
            status_code=http_status.HTTP_400_BAD_REQUEST,
            detail="channel is not active in this campaign",
        ) from exc
    if board is None:
        raise HTTPException(status_code=http_status.HTTP_404_NOT_FOUND, detail="campaign not found")
    return board


@router.delete(
    "/campaigns/{campaign_id}",
    status_code=http_status.HTTP_204_NO_CONTENT,
    operation_id="deleteCampaign",
)
async def delete_campaign(campaign_id: str) -> None:
    """Delete a campaign and all its serving links, channels, and comments."""
    await nc_service.delete_campaign(campaign_id)


@router.post(
    "/campaigns/{campaign_id}/channels/remove",
    status_code=http_status.HTTP_204_NO_CONTENT,
    operation_id="removeCampaignChannel",
)
async def remove_channel(campaign_id: str, body: LinkChannelRequest) -> None:
    """Detach a channel from a campaign (frees its slot for another campaign)."""
    await nc_service.deactivate_channel(campaign_id, body.channel)


@router.put(
    "/campaigns/{campaign_id}/prompt",
    status_code=http_status.HTTP_204_NO_CONTENT,
    operation_id="updateCampaignPrompt",
)
async def update_prompt(campaign_id: str, body: UpdatePromptRequest) -> None:
    """Replace a campaign's generation prompt (the edit-prompt modal)."""
    await nc_service.update_campaign_prompt(campaign_id, body.prompt)


@router.post(
    "/campaigns/{campaign_id}/solver",
    status_code=http_status.HTTP_204_NO_CONTENT,
    operation_id="setCampaignSolver",
)
async def set_campaign_solver(campaign_id: str, body: SolverToggleRequest) -> None:
    """Turn the campaign's challenge (captcha) solver on/off."""
    await nc_service.set_solver_enabled(campaign_id, body.enabled)


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
    "/campaigns/{campaign_id}/challenges/counts",
    response_model=ChallengeOutcomeCounts,
    operation_id="countCampaignChallengeOutcomes",
)
async def count_campaign_challenge_outcomes(
    campaign_id: str,
    since: Annotated[str, Query(min_length=1)],
) -> ChallengeOutcomeCounts:
    """Challenge-outcome counters (solved/failed/give_up/pending) across a campaign (#148)."""
    return await nc_service.count_campaign_challenge_outcomes(campaign_id, since)


@router.get(
    "/channels/challenges",
    response_model=ChallengeRowList,
    operation_id="listChannelChallenges",
)
async def list_channel_challenges(
    channel: Annotated[str, Query(min_length=1)],
    limit: Annotated[int, Query(ge=1, le=100)] = 20,
) -> ChallengeRowList:
    """Recent unsolved bot-challenges for one channel — the work-view drill-down (#148)."""
    return await nc_service.list_channel_challenges(channel, limit)


@router.post(
    "/skip",
    status_code=http_status.HTTP_204_NO_CONTENT,
    operation_id="skipNeurocommentPair",
)
async def skip_pair(body: RetryPairRequest) -> None:
    """Operator "Skip channel for this account": the engine never selects the pair (#148)."""
    await nc_service.skip_pair(body.account_id, body.channel)


@router.post(
    "/campaigns/{campaign_id}/status",
    status_code=http_status.HTTP_204_NO_CONTENT,
    operation_id="setCampaignStatus",
)
async def set_campaign_status(campaign_id: str, body: SetCampaignStatusRequest) -> None:
    """Per-campaign run/pause: flip a campaign between active and paused (#148)."""
    await nc_service.set_campaign_status(campaign_id, body.status)


@router.get(
    "/runtime",
    response_model=NeurocommentRuntimeStatus,
    operation_id="getNeurocommentRuntime",
)
async def get_runtime() -> NeurocommentRuntimeStatus:
    return await nc_service.neurocomment_runtime_status()


@contextmanager
def _listener_conflicts_translated() -> Iterator[None]:
    """The three runtimes that can hold a picked listener, as stable 409 codes.

    Shared by the two routes that point the listener at an account, so both buttons
    report a conflict the same way.
    """
    try:
        yield
    except nc_service.ListenerBusyWarmingError as exc:
        raise HTTPException(
            status_code=http_status.HTTP_409_CONFLICT,
            detail="listener account is currently warming",
        ) from exc
    except nc_service.ListenerBusyDiscoveryError as exc:
        # The same stable code warming's start reports for the same condition, so one
        # translation covers both buttons.
        raise HTTPException(
            status_code=http_status.HTTP_409_CONFLICT,
            detail=DISCOVERY_BUSY_CODE,
        ) from exc
    except nc_service.ListenerBusyNeuroshillingError as exc:
        # A code of its own rather than warming's ``account_busy_neuroshilling``: the
        # condition is the same but the next move is not, and that copy tells the
        # operator to start warming afterwards.
        raise HTTPException(
            status_code=http_status.HTTP_409_CONFLICT,
            detail=LISTENER_BUSY_NEUROSHILLING_CODE,
        ) from exc


@router.post(
    "/start",
    response_model=NeurocommentRuntimeStatus,
    operation_id="startNeurocomment",
    responses=error_responses(409),
)
async def start(body: StartNeurocommentRequest) -> NeurocommentRuntimeStatus:
    with _listener_conflicts_translated():
        await nc_service.start_neurocomment(body.listener_account_id)
    return await nc_service.neurocomment_runtime_status()


@router.post("/stop", response_model=NeurocommentRuntimeStatus, operation_id="stopNeurocomment")
async def stop() -> NeurocommentRuntimeStatus:
    """Pause the runtime: unsubscribe but keep the remembered listener account."""
    await nc_service.stop_neurocomment()
    return await nc_service.neurocomment_runtime_status()


@router.post(
    "/listener",
    response_model=NeurocommentRuntimeStatus,
    operation_id="setNeurocommentListener",
    responses=error_responses(409),
)
async def set_listener(body: StartNeurocommentRequest) -> NeurocommentRuntimeStatus:
    """Remember the picked listener without starting the engine ("Сохранить" in the modal)."""
    with _listener_conflicts_translated():
        remembered = await nc_service.remember_neurocomment_listener(body.listener_account_id)
    if remembered:
        return await nc_service.neurocomment_runtime_status()
    # The engine is running, so this is a live hand-off rather than a bookmark, and only
    # /start performs one. Not atomic with the check above: a Stop landing in the gap
    # turns this into a start the operator did not press — a millisecond window, and the
    # alternative is holding the lifecycle lock across the whole reconcile.
    return await start(body)


@router.post(
    "/listener/clear",
    response_model=NeurocommentRuntimeStatus,
    operation_id="clearNeurocommentListener",
)
async def clear_listener() -> NeurocommentRuntimeStatus:
    """Remove the listener ("снять слушателя"): unsubscribe and forget the account."""
    await nc_service.clear_neurocomment_listener()
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


@router.get(
    "/accounts/{account_id}/limits",
    response_model=AccountLimitsView,
    operation_id="getAccountLimits",
    responses=error_responses(404),
)
async def get_account_limits(account_id: str) -> AccountLimitsView:
    """One account's caps, what each window has spent, and when a slot comes back."""
    view = await nc_service.load_account_limits(account_id)
    if view is None:
        raise HTTPException(status_code=http_status.HTTP_404_NOT_FOUND, detail="account not found")
    return view


@router.put(
    "/accounts/{account_id}/limits",
    response_model=AccountLimitsView,
    operation_id="updateAccountLimits",
    responses=error_responses(404),
)
async def update_account_limits(account_id: str, body: AccountLimitsUpdate) -> AccountLimitsView:
    """Replace the account's overrides — a null field drops back to the fleet cap."""
    view = await nc_service.save_account_limits(account_id, body)
    if view is None:
        raise HTTPException(status_code=http_status.HTTP_404_NOT_FOUND, detail="account not found")
    return view
