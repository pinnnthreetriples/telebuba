"""Health probe — cheap liveness check that touches no I/O."""

from __future__ import annotations

from fastapi import APIRouter

from api.errors import error_responses
from schemas.api import HealthStatus

# Unauthenticated and parameterless, so 500 is the only error it can answer.
router = APIRouter(tags=["health"], responses=error_responses(500))


@router.get("/health", response_model=HealthStatus, operation_id="getHealth")
async def health() -> HealthStatus:
    return HealthStatus()
