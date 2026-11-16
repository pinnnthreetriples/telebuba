"""Which Telebuba instance may talk to Telegram for the fleet: one, the fleet owner.

Why: the same account keys exist on more than one computer (a laptop copy of the main PC's
sessions). Two instances on one key, from two IPs, is what ``AUTH_KEY_DUPLICATED`` and
Telegram's anti-fraud punish, and two runtimes would also act twice. Nothing stopped that.

``INSTANCE__FLEET_OWNER_HOST`` names the owner's hostname. Set (on every computer that holds
copies), only the computer with that hostname builds Telegram clients; any other refuses
every one (:class:`FleetOwnerRefusedError`, a ``ConnectionError``, which every caller already
treats as an unreachable account). Empty: no restriction, as before.
"""

from __future__ import annotations

import socket

from core.config import settings

__all__ = ["FleetOwnerRefusedError", "fleet_owner_host", "is_fleet_owner", "require_fleet_owner"]


class FleetOwnerRefusedError(ConnectionError):
    """This computer is not the fleet owner: it must not connect any account to Telegram."""


def fleet_owner_host() -> str:
    return settings.instance.fleet_owner_host.strip()


def is_fleet_owner() -> bool:
    owner = fleet_owner_host()
    return not owner or owner.casefold() == socket.gethostname().casefold()


def require_fleet_owner() -> None:
    if not is_fleet_owner():
        msg = "this computer is not the fleet owner (INSTANCE__FLEET_OWNER_HOST)"
        raise FleetOwnerRefusedError(msg)
