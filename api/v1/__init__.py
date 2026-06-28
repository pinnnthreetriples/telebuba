"""Versioned ``/api/v1`` router assembly."""

from __future__ import annotations

from fastapi import APIRouter, Depends

from api.deps import get_current_user
from api.v1 import accounts, auth, health, neurocomment, warming

router = APIRouter()
# Unprotected: auth (login/logout; /me self-guards) and the liveness probe.
router.include_router(auth.router)
router.include_router(health.router)
# Everything else requires a valid session.
_protected = [Depends(get_current_user)]
router.include_router(accounts.router, dependencies=_protected)
router.include_router(warming.router, dependencies=_protected)
router.include_router(neurocomment.router, dependencies=_protected)

__all__ = ["router"]
