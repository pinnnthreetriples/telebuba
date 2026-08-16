"""Start and Stop — split sibling of ``neuroshilling.py``, mounted onto its router.

Same arrangement ``_neuroshilling_scenario.py`` uses: no prefix and no tags of its
own, so the paths and the OpenAPI grouping come from the parent.

There is no run-status endpoint. The status is a field of ``NeuroshillingBoard``,
which the page already polls and already invalidates from the log stream, and a second
route serving the same numbers could only disagree with the first.

Both statuses are raised as literals inside the route bodies rather than through a
shared mapping: ``tests/test_api_error_contract`` derives what an operation can answer
by reading the literal ``status_code=`` of every raise reachable from it.
"""

from __future__ import annotations

from fastapi import APIRouter, HTTPException
from fastapi import status as http_status

from api.errors import error_responses
from schemas.neuroshilling import NeuroshillingRunStatus
from services import neuroshilling as ns_service

run_router = APIRouter()

_NOT_FOUND = "campaign not found"


@run_router.post(
    "/campaigns/{campaign_id}/start",
    response_model=NeuroshillingRunStatus,
    operation_id="startNeuroshillingCampaign",
    responses=error_responses(404, 409),
)
async def start_campaign(campaign_id: str) -> NeuroshillingRunStatus:
    """Begin playing the approved dialogue into the campaign's targets.

    409 covers every reason a run cannot begin: the scenario is still a draft, the
    roster is short or leaves a role unstaffed, there are no targets, an account is
    held by another feature, the campaign is already running, or ``run_mode`` is
    ``parallel`` — which this build refuses on the server rather than merely hiding,
    because the generated client types the field.
    """
    try:
        status = await ns_service.start_campaign(campaign_id)
    except ns_service.NeuroshillingConflictError as exc:
        raise HTTPException(status_code=http_status.HTTP_409_CONFLICT, detail=exc.code) from exc
    if status is None:
        raise HTTPException(status_code=http_status.HTTP_404_NOT_FOUND, detail=_NOT_FOUND)
    return status


@run_router.post(
    "/campaigns/{campaign_id}/stop",
    response_model=NeuroshillingRunStatus,
    operation_id="stopNeuroshillingCampaign",
    responses=error_responses(404),
)
async def stop_campaign(campaign_id: str) -> NeuroshillingRunStatus:
    """Fence the run, cancel it, and answer with where it ended up.

    Idempotent: stopping a campaign that is already idle answers its current status
    rather than a conflict. By the time a click lands the run may have finished a
    second ago, and "conflict" is noise rather than information about that.
    """
    status = await ns_service.stop_campaign(campaign_id)
    if status is None:
        raise HTTPException(status_code=http_status.HTTP_404_NOT_FOUND, detail=_NOT_FOUND)
    return status
