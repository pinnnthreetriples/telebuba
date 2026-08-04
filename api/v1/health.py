"""Health probes — liveness (no I/O) and readiness (dependencies reachable)."""

from __future__ import annotations

from fastapi import APIRouter, Response
from fastapi import status as http_status

from schemas.api import HealthStatus, ReadinessStatus
from services import health as health_service

router = APIRouter(tags=["health"])


@router.get("/health", response_model=HealthStatus, operation_id="getHealth")
async def health() -> HealthStatus:
    return HealthStatus()


@router.get(
    "/ready",
    response_model=ReadinessStatus,
    operation_id="getReadiness",
    responses={
        http_status.HTTP_503_SERVICE_UNAVAILABLE: {
            "model": ReadinessStatus,
            "description": "A dependency is unreachable",
        },
    },
)
async def ready(response: Response) -> ReadinessStatus:
    """Answer 503 while a dependency is down, so a supervisor can hold traffic back.

    The one route that answers a non-2xx with something other than the
    ``ErrorEnvelope``, deliberately: this is consumed by an orchestrator's probe,
    not the SPA, and the per-dependency verdict is the whole point of the body.
    The status CODE has to carry the signal — a 200 saying "unavailable" is
    invisible to every probe implementation.
    """
    result = await health_service.check_readiness()
    if result.status != "ok":
        response.status_code = http_status.HTTP_503_SERVICE_UNAVAILABLE
    return result
