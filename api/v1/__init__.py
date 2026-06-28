"""Versioned ``/api/v1`` router assembly."""

from __future__ import annotations

from fastapi import APIRouter, Depends

from api.deps import get_current_user
from api.v1 import accounts, auth, health

router = APIRouter()
# Unprotected: auth (login/logout; /me self-guards) and the liveness probe.
router.include_router(auth.router)
router.include_router(health.router)
# Everything else requires a valid session.
router.include_router(accounts.router, dependencies=[Depends(get_current_user)])

__all__ = ["router"]
