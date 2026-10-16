"""The lead queue: add numbers, watch their status, remove one, configure and pause it."""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query

from api.deps import get_current_user
from api.errors import error_responses
from schemas.auth import (
    UserRead,  # noqa: TC001 - FastAPI resolves dependency annotations at runtime
)
from schemas.lead_queue import (
    MAX_LISTED_LEADS,
    LeadQueueAddRequest,
    LeadQueueAddResult,
    LeadQueueSettings,
    LeadQueueSettingsUpdate,
    LeadQueueView,
)
from services.accounts import lead_queue

_DEFAULT_LISTED = 200

lead_queue_router = APIRouter()


@lead_queue_router.get(
    "/accounts/lead-queue",
    response_model=LeadQueueView,
    operation_id="getLeadQueue",
)
async def get_lead_queue(
    limit: Annotated[int, Query(ge=1, le=MAX_LISTED_LEADS)] = _DEFAULT_LISTED,
) -> LeadQueueView:
    return await lead_queue.get_lead_queue(limit)


@lead_queue_router.post(
    "/accounts/lead-queue/leads",
    response_model=LeadQueueAddResult,
    operation_id="addLeadsToQueue",
)
async def add_leads_to_queue(body: LeadQueueAddRequest) -> LeadQueueAddResult:
    return await lead_queue.add_leads(body)


@lead_queue_router.delete(
    "/accounts/lead-queue/leads/{lead_id}",
    status_code=204,
    operation_id="removeQueuedLead",
    responses=error_responses(404, 409),
)
async def remove_queued_lead(lead_id: int) -> None:
    outcome = await lead_queue.remove_lead(lead_id)
    if outcome == "not_found":
        raise HTTPException(status_code=404, detail="lead not found")
    if outcome == "busy":
        raise HTTPException(status_code=409, detail="lead_in_progress")


@lead_queue_router.put(
    "/accounts/lead-queue/settings",
    response_model=LeadQueueSettings,
    operation_id="updateLeadQueueSettings",
)
async def update_lead_queue_settings(
    body: LeadQueueSettingsUpdate,
    user: Annotated[UserRead, Depends(get_current_user)],
) -> LeadQueueSettings:
    return await lead_queue.update_lead_queue_settings(body, user.id)
