"""Versioned ``/api/v1`` router assembly."""

from __future__ import annotations

from fastapi import APIRouter

from api.v1 import accounts, health

router = APIRouter()
router.include_router(health.router)
router.include_router(accounts.router)

__all__ = ["router"]
