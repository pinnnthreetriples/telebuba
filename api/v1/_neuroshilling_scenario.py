"""Scenario endpoints — split sibling of ``neuroshilling.py``, mounted onto its router.

Same arrangement ``_neurocomment_discovery.py`` uses: no prefix and no tags of its
own, so the paths and the OpenAPI grouping come from the parent.

Four operations. Roles and steps share ONE ``PUT`` because they must be written in
one transaction — two endpoints would leave a window in which a step points at a
role the other call has already deleted. There is no preview endpoint: the client
holds the roles and the steps and draws the preview from them.

A provider failure answers **503**, never 502: ``api.errors`` carries no
description for 502, so ``error_responses(502)`` would raise at import time.
"""

from __future__ import annotations

from contextlib import contextmanager
from typing import TYPE_CHECKING

from fastapi import APIRouter, HTTPException
from fastapi import status as http_status

from api.errors import error_responses
from schemas.neuroshilling_scenario import (
    NeuroshillingGenerateRequest,
    NeuroshillingScenario,
    NeuroshillingScenarioUpdate,
)
from services import neuroshilling as ns_service

if TYPE_CHECKING:
    from collections.abc import Iterator

scenario_router = APIRouter()

_NOT_FOUND = "campaign not found"


@contextmanager
def _refusals() -> Iterator[None]:
    """The two refusals EVERY scenario route can answer, mapped in one place.

    ``detail`` carries ``exc.code`` and nothing else: the SPA translates the code,
    and a refusal has no per-field payload to add.

    The statuses are written as literals rather than looked up in a map, and the
    provider refusal is deliberately not here. ``tests/test_api_error_contract``
    derives what an operation can answer by reading the literal ``status_code`` of
    every raise reachable from it: a mapping lookup is invisible to that scan and
    would silently drop 400/409 from all three declarations, while folding 503 in
    here would credit the PUT and the approval with a status only generation can
    answer. Registering a handler in ``api.errors`` instead would do the same to
    every route in the app.
    """
    try:
        yield
    except ns_service.NeuroshillingConflictError as exc:
        raise HTTPException(status_code=http_status.HTTP_409_CONFLICT, detail=exc.code) from exc
    except ns_service.NeuroshillingInvalidError as exc:
        raise HTTPException(status_code=http_status.HTTP_400_BAD_REQUEST, detail=exc.code) from exc


def _found(scenario: NeuroshillingScenario | None) -> NeuroshillingScenario:
    if scenario is None:
        raise HTTPException(status_code=http_status.HTTP_404_NOT_FOUND, detail=_NOT_FOUND)
    return scenario


@scenario_router.get(
    "/campaigns/{campaign_id}/scenario",
    response_model=NeuroshillingScenario,
    operation_id="getNeuroshillingScenario",
    responses=error_responses(404),
)
async def get_scenario(campaign_id: str) -> NeuroshillingScenario:
    """Roles, steps and the approval status — a read of its own, not a board field.

    Separate so the SPA can keep it OUT of the log-stream invalidation set: the
    stream fires on every log line, and a scenario form refetched under the
    operator's typing would lose what they were writing.
    """
    return _found(await ns_service.load_scenario(campaign_id))


@scenario_router.put(
    "/campaigns/{campaign_id}/scenario",
    response_model=NeuroshillingScenario,
    operation_id="setNeuroshillingScenario",
    responses=error_responses(400, 404, 409),
)
async def set_scenario(
    campaign_id: str,
    body: NeuroshillingScenarioUpdate,
) -> NeuroshillingScenario:
    """Save the whole dialogue. ALWAYS returns the campaign to ``draft``."""
    with _refusals():
        scenario = await ns_service.set_scenario(campaign_id, body)
    return _found(scenario)


@scenario_router.post(
    "/campaigns/{campaign_id}/generate",
    response_model=NeuroshillingScenario,
    operation_id="generateNeuroshillingScenario",
    responses=error_responses(400, 404, 409, 503),
)
async def generate_scenario(
    campaign_id: str,
    body: NeuroshillingGenerateRequest,
) -> NeuroshillingScenario:
    """Write a fresh dialogue with the LLM, replacing whatever the campaign had.

    409 is a second click (``generation_in_progress``) or the rolling daily budget
    (``llm_daily_limit_reached``); 503 is the provider having produced nothing
    usable within its retry budget, or no key being configured at all.
    """
    try:
        with _refusals():
            scenario = await ns_service.generate_scenario(campaign_id, body)
    except ns_service.NeuroshillingUnavailableError as exc:
        # The one refusal only THIS route can answer, so it is declared and raised
        # only here — see :func:`_refusals`.
        raise HTTPException(
            status_code=http_status.HTTP_503_SERVICE_UNAVAILABLE,
            detail=exc.code,
        ) from exc
    return _found(scenario)


@scenario_router.post(
    "/campaigns/{campaign_id}/approve",
    response_model=NeuroshillingScenario,
    operation_id="approveNeuroshillingScenario",
    responses=error_responses(400, 404, 409),
)
async def approve_scenario(campaign_id: str) -> NeuroshillingScenario:
    """The ONLY way ``scenario_status`` becomes ``approved``, and it validates first."""
    with _refusals():
        scenario = await ns_service.approve_scenario(campaign_id)
    return _found(scenario)
