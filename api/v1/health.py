"""Health probe — cheap liveness check that touches no I/O."""

from __future__ import annotations

from fastapi import APIRouter

from schemas.api import HealthStatus

router = APIRouter(tags=["health"])


@router.get("/health", response_model=HealthStatus)
async def health() -> HealthStatus:
    return HealthStatus()
