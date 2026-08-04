"""Versioned ``/api/v1`` router assembly."""

from __future__ import annotations

from fastapi import APIRouter, Depends

from api.deps import get_current_user
from api.errors import PROTECTED_ERRORS
from api.v1 import (
    accounts,
    accounts_media,
    auth,
    events,
    health,
    logs,
    neurocomment,
    proxies,
    warming,
)

router = APIRouter()
# Unprotected: auth (login/logout; /me self-guards) and the health probes —
# liveness and readiness both answer a supervisor that holds no session.
router.include_router(auth.router)
router.include_router(health.router)
# Everything else requires a valid session. The gate and the error statuses it
# implies (401 + the always-possible 422/500) are declared in the same place, so a
# router mounted here cannot get the dependency without the matching contract.
_protected = [Depends(get_current_user)]
router.include_router(accounts.router, dependencies=_protected, responses=PROTECTED_ERRORS)
router.include_router(accounts_media.router, dependencies=_protected, responses=PROTECTED_ERRORS)
router.include_router(proxies.router, dependencies=_protected, responses=PROTECTED_ERRORS)
router.include_router(warming.router, dependencies=_protected, responses=PROTECTED_ERRORS)
router.include_router(neurocomment.router, dependencies=_protected, responses=PROTECTED_ERRORS)
router.include_router(logs.router, dependencies=_protected, responses=PROTECTED_ERRORS)
router.include_router(events.router, dependencies=_protected, responses=PROTECTED_ERRORS)

__all__ = ["router"]
