"""The container layer's one failure type: a stable code, never third-party text."""

from __future__ import annotations

from typing import TYPE_CHECKING, Literal

if TYPE_CHECKING:
    from schemas.emulator import EmulatorRefusalCode

# Why a pod's gateway failed its barrier check: stable words, never an address.
GatewayFault = Literal[
    "tunnel_down",
    "kill_switch_missing",
    "kill_switch_unreadable",
    "egress_open",
    "route_not_tunnel",
    "check_timeout",
    "check_failed",
]


class EmulatorError(RuntimeError):
    """A container step failed; ``code`` is what the operator is shown.

    Console and adb output is never put in the message: an adb argv can carry the
    account's proxy credentials, and docker's text is unbounded.
    """

    def __init__(self, code: EmulatorRefusalCode) -> None:
        super().__init__(code)
        self.code: EmulatorRefusalCode = code


class GatewayUnhealthyError(EmulatorError):
    """An existing pod's gateway no longer holds its barrier; ``reason`` says which part.

    Its code is ``emulator_gateway_unhealthy``. ``reason`` is journalled, so like the
    code it names the failed check only: never a proxy address, rule text or output.
    """

    def __init__(self, reason: GatewayFault) -> None:
        super().__init__("emulator_gateway_unhealthy")
        self.reason: GatewayFault = reason
