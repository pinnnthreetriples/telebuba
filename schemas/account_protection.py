"""Contracts for the per-account protection summary shown on the Accounts table.

An account the operator bought must stay his: this lists, from stored facts only, which of
the protections are in place. No behaviour, no I/O.
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field

# In the order the checks are listed and reported as missing.
ProtectionCheck = Literal[
    "proxy",
    "session_copy",
    "own_key",
    "old_key_closed",
    "twofa_stored",
    "passkey",
    "audit_clean",
]
PROTECTION_CHECKS: tuple[ProtectionCheck, ...] = (
    "proxy",
    "session_copy",
    "own_key",
    "old_key_closed",
    "twofa_stored",
    "passkey",
    "audit_clean",
)


class AccountProtection(BaseModel):
    """How many of the protections an account has, and which are missing."""

    score: int = Field(ge=0)
    total: int = Field(ge=1)
    missing: list[ProtectionCheck] = Field(default_factory=list)
