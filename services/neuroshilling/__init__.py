"""Neuroshilling engine (package) — scripted multi-account dialogue in target chats.

Pure business logic: the DB is reached only through
``core.repositories.neuroshilling`` and Telegram / the LLMs only through
``services.neuroshilling._seams``, so tests patch one place.

This module is re-export only; the submodules own the behaviour.
"""

from __future__ import annotations

# Imported after the two modules above because ``_runtime`` pulls in the engine, which
# pulls in ``campaigns`` — the same one-way order every other submodule here follows.
from services.neuroshilling._runtime import (
    reconcile_neuroshilling_on_startup,
    shutdown_neuroshilling_on_shutdown,
    start_campaign,
    stop_campaign,
)
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
    run_status,
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
    "reconcile_neuroshilling_on_startup",
    "run_status",
    "set_scenario",
    "shutdown_neuroshilling_on_shutdown",
    "start_campaign",
    "stop_campaign",
    "update_campaign",
]
