"""Neuroshilling engine (package) — scripted multi-account dialogue in target chats.

Pure business logic: the DB is reached only through
``core.repositories.neuroshilling`` and Telegram / the LLMs only through
``services.neuroshilling._seams``, so tests patch one place.

This module is re-export only; the submodules own the behaviour.
"""

from __future__ import annotations

from services.neuroshilling.campaigns import (
    NeuroshillingConflictError,
    NeuroshillingInvalidError,
    NeuroshillingRefusedError,
    create_campaign,
    delete_campaign,
    list_campaigns,
    load_board,
    parse_targets,
    update_campaign,
)

__all__ = [
    "NeuroshillingConflictError",
    "NeuroshillingInvalidError",
    "NeuroshillingRefusedError",
    "create_campaign",
    "delete_campaign",
    "list_campaigns",
    "load_board",
    "parse_targets",
    "update_campaign",
]
