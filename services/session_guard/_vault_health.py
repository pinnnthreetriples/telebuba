"""The key vault's health, asked once per guard pass and journalled only when it changes.

The per-account check (``services.accounts.passkey.check_passkey_vault``) runs from the watch
and says nothing about a vault that is simply down while no account is due. This step asks
the vault itself (``core.passkey_vault.vault_health``) and keeps the last answer for the
status page. A problem is logged (``passkey_vault_unhealthy``, extra ``state`` only, no
account) when the state CHANGES to a bad one, or at the first pass if it starts bad: never
on every pass, or the log would fill with one row per ``SESSION_AUDIT__GUARD_INTERVAL_SECONDS``.
"""

from __future__ import annotations

from dataclasses import dataclass

from core.logging import log_event
from core.passkey_vault import VaultHealth, vault_health

# ``off`` (no token: passkeys are not in use) and ``ok`` are not problems.
_PROBLEM_STATES: frozenset[VaultHealth] = frozenset({"sealed", "unreachable", "rejected"})


@dataclass
class _State:
    last: VaultHealth | None = None


_state = _State()


def last_vault_state() -> VaultHealth | None:
    """The state the last pass found; ``None`` before the first pass."""
    return _state.last


async def run_vault_health_check() -> None:
    """Ask the vault, remember the answer, journal a change to a bad state. Never raises."""
    state = await vault_health()
    previous, _state.last = _state.last, state
    if state in _PROBLEM_STATES and state != previous:
        await log_event("WARNING", "passkey_vault_unhealthy", extra={"state": state})


def reset_for_tests() -> None:
    global _state  # noqa: PLW0603 - one module-level runtime, reset between tests
    _state = _State()
