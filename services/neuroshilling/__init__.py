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
    NeuroshillingUnavailableError,
    create_campaign,
    delete_campaign,
    list_campaigns,
    load_board,
    parse_targets,
    update_campaign,
)
from services.neuroshilling.scenario import (
    approve_scenario,
    generate_scenario,
    load_scenario,
    set_scenario,
)

__all__ = [
    "NeuroshillingConflictError",
    "NeuroshillingInvalidError",
    "NeuroshillingRefusedError",
    "NeuroshillingUnavailableError",
    "approve_scenario",
    "create_campaign",
    "delete_campaign",
    "generate_scenario",
    "list_campaigns",
    "load_board",
    "load_scenario",
    "parse_targets",
    "set_scenario",
    "update_campaign",
]
