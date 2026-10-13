"""Read and save the outreach limits that pace the contact lookup and bulk messages."""

from __future__ import annotations

from fastapi import APIRouter

from schemas.outreach_limits import OutreachLimits, OutreachLimitsUpdate
from services.accounts import outreach_limits

outreach_limits_router = APIRouter()


@outreach_limits_router.get(
    "/accounts/outreach-limits",
    response_model=OutreachLimits,
    operation_id="getOutreachLimits",
)
async def get_outreach_limits() -> OutreachLimits:
    return await outreach_limits.load_outreach_limits()


@outreach_limits_router.put(
    "/accounts/outreach-limits",
    response_model=OutreachLimits,
    operation_id="updateOutreachLimits",
)
async def update_outreach_limits(body: OutreachLimitsUpdate) -> OutreachLimits:
    return await outreach_limits.save_outreach_limits(body)
