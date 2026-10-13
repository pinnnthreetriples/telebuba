"""Outreach pacing: the saved limits, today's usage per account and saved presets."""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, HTTPException, Query

from api.errors import SERVICE_ERRORS, error_responses
from api.v1._errors import service_errors_to_http
from schemas.outreach_limits import OutreachLimits, OutreachLimitsUpdate, OutreachUsageList
from schemas.outreach_presets import OutreachPreset, OutreachPresetList, OutreachPresetWrite
from services.accounts import outreach_limits

_MAX_USAGE_ACCOUNTS = 50

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


@outreach_limits_router.get(
    "/accounts/outreach-usage",
    response_model=OutreachUsageList,
    operation_id="getOutreachUsageToday",
)
async def get_outreach_usage_today(
    account_ids: Annotated[list[str], Query(max_length=_MAX_USAGE_ACCOUNTS)],
) -> OutreachUsageList:
    return await outreach_limits.list_outreach_usage_today(account_ids)


@outreach_limits_router.get(
    "/accounts/outreach-presets",
    response_model=OutreachPresetList,
    operation_id="listOutreachPresets",
)
async def list_outreach_presets() -> OutreachPresetList:
    return await outreach_limits.list_outreach_presets()


@outreach_limits_router.post(
    "/accounts/outreach-presets",
    response_model=OutreachPreset,
    status_code=201,
    operation_id="createOutreachPreset",
    responses=SERVICE_ERRORS,
)
async def create_outreach_preset(body: OutreachPresetWrite) -> OutreachPreset:
    with service_errors_to_http():
        return await outreach_limits.create_outreach_preset(body)


@outreach_limits_router.put(
    "/accounts/outreach-presets/{preset_id}",
    response_model=OutreachPreset,
    operation_id="updateOutreachPreset",
    responses=SERVICE_ERRORS,
)
async def update_outreach_preset(preset_id: str, body: OutreachPresetWrite) -> OutreachPreset:
    with service_errors_to_http():
        preset = await outreach_limits.update_outreach_preset(preset_id, body)
    if preset is None:
        raise HTTPException(status_code=404, detail="preset not found")
    return preset


@outreach_limits_router.delete(
    "/accounts/outreach-presets/{preset_id}",
    status_code=204,
    operation_id="deleteOutreachPreset",
    responses=error_responses(404),
)
async def delete_outreach_preset(preset_id: str) -> None:
    if not await outreach_limits.delete_outreach_preset(preset_id):
        raise HTTPException(status_code=404, detail="preset not found")
