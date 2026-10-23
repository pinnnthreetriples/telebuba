"""Who holds a mobile account's pod right now, and the refusals that say so.

Split out of ``mobile_emulator`` for the file-size budget; it re-exports all of it.
Process-local, like the rest of the runtime state (one uvicorn worker).
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from core.config import settings
from services.accounts._result import AccountActionError

if TYPE_CHECKING:
    import asyncio

    from schemas.device_farm import PhoneStatus
    from schemas.emulator import EmulatorRefusalCode, PlaybookResult

__all__ = [
    "EmulatorBusyError",
    "EmulatorRefusedError",
    "claim_manual",
    "container_name",
    "phone_activity",
    "release_manual",
]

_LOCKS: dict[str, asyncio.Lock] = {}
# Accounts with an activity session running; accounts an operator controls by hand.
_ACTIVE: set[str] = set()
_MANUAL: set[str] = set()
# Accounts whose pod the operator opened (``mobile_emulator.operator_holds``).
_OPERATOR: set[str] = set()


class EmulatorRefusedError(AccountActionError):
    """The container could not be opened; ``code`` is the stable code the SPA translates.

    ``partial`` is what a failed activity session did before it failed, else ``None``.
    """

    def __init__(self, code: EmulatorRefusalCode, *, partial: PlaybookResult | None = None) -> None:
        super().__init__(code)
        self.partial = partial


class EmulatorBusyError(EmulatorRefusedError):
    """Another start of the same account's container is still running (409)."""

    def __init__(self) -> None:
        super().__init__("emulator_busy")


def container_name(account_id: str) -> str:
    return f"{settings.emulator.instance_prefix}{account_id}"


def phone_activity(account_id: str) -> PhoneStatus | None:
    """What holds the account's pod right now, as far as this process knows."""
    lock = _LOCKS.get(account_id)
    if lock is not None and lock.locked():
        return "starting"
    if account_id in _ACTIVE:
        return "warming"
    if account_id in _MANUAL:
        return "manual"
    return None


def claim_manual(account_id: str) -> None:
    """Hand the pod's controls to an operator; ``emulator_busy`` while anything holds it."""
    if phone_activity(account_id) is not None:
        raise EmulatorBusyError
    _MANUAL.add(account_id)


def release_manual(account_id: str) -> None:
    """Give the controls back; with no desktop window, the operator's open is done too."""
    _MANUAL.discard(account_id)
    if not settings.emulator.desktop_viewer:
        _OPERATOR.discard(account_id)
