"""Contracts for an independent emulator: a phone of its own, bound to no account.

It has the same Android pod a mobile account has, with its own pool proxy and no app
signed in; the operator works it by hand on the device farm's screen. No behaviour, no I/O.
"""

from __future__ import annotations

from pydantic import BaseModel, Field

from schemas.device_fingerprint import DeviceFingerprint  # noqa: TC001

_LABEL_MAX = 64


class StandaloneEmulator(BaseModel):
    """One stored independent emulator; ``fingerprint.account_id`` is its own ``id``."""

    id: str = Field(min_length=1)
    label: str | None = None
    proxy_id: str | None = None
    fingerprint: DeviceFingerprint
    created_at: str


class StandaloneEmulatorCreate(BaseModel):
    label: str | None = Field(default=None, max_length=_LABEL_MAX)
    proxy_id: str | None = Field(default=None, min_length=1)


class StandaloneProxyAssign(BaseModel):
    proxy_id: str = Field(min_length=1)
