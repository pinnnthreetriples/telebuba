"""Versioned ``/api/v1`` router assembly."""

from __future__ import annotations

from fastapi import APIRouter, Depends

from api.deps import get_current_user
from api.errors import PROTECTED_ERRORS
from api.v1 import (
    account_folders,
    accounts,
    accounts_media,
    auth,
    chat_broadcast,
    devices,
    events,
    health,
    logs,
    masstagging,
    neurocomment,
    neuroshilling,
    notifications,
    proxies,
    telephony,
    user_parser,
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
# Mounted after the routers that predate it: mount order is what orders ``paths``
# in the generated OpenAPI document, so appending keeps the generated client's
# diff an append too, whatever the reader's diff algorithm makes of the rest.
router.include_router(neuroshilling.router, dependencies=_protected, responses=PROTECTED_ERRORS)
router.include_router(chat_broadcast.router, dependencies=_protected, responses=PROTECTED_ERRORS)
router.include_router(account_folders.router, dependencies=_protected, responses=PROTECTED_ERRORS)
router.include_router(user_parser.router, dependencies=_protected, responses=PROTECTED_ERRORS)
router.include_router(masstagging.router, dependencies=_protected, responses=PROTECTED_ERRORS)
router.include_router(notifications.router, dependencies=_protected, responses=PROTECTED_ERRORS)
router.include_router(devices.router, dependencies=_protected, responses=PROTECTED_ERRORS)
router.include_router(telephony.router, dependencies=_protected, responses=PROTECTED_ERRORS)
# Self-guarded: a WebSocket handshake authenticates itself (see ``api.v1.devices``).
router.include_router(devices.screen_router)
# Self-guarded: SMS gateways send a shared secret instead (see ``api.v1.telephony``).
router.include_router(telephony.webhook_router)

__all__ = ["router"]
