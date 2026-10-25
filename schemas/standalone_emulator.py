"""Contracts for an independent emulator: a phone of its own, bound to no account.

It has the same Android pod a mobile account has, with its own pool proxy and no app
signed in; the operator works it by hand on the device farm's screen. ``install_apps`` are
the apps its start installs when the phone lacks them, picked when it is added. No
behaviour, no I/O.
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field

from schemas.device_fingerprint import DeviceFingerprint  # noqa: TC001

_LABEL_MAX = 64

# ``telegram``: telegram.org's APK; ``gosuslugi``: the Госуслуги app from RuStore.
StandaloneApp = Literal["telegram", "gosuslugi"]


class StandaloneEmulator(BaseModel):
    """One stored independent emulator; ``fingerprint.account_id`` is its own ``id``."""

    id: str = Field(min_length=1)
    label: str | None = None
    proxy_id: str | None = None
    install_apps: list[StandaloneApp]
    fingerprint: DeviceFingerprint
    created_at: str


def _telegram_only() -> list[StandaloneApp]:
    return ["telegram"]


class StandaloneEmulatorCreate(BaseModel):
    label: str | None = Field(default=None, max_length=_LABEL_MAX)
    proxy_id: str | None = Field(default=None, min_length=1)
    install_apps: list[StandaloneApp] = Field(default_factory=_telegram_only)


class StandaloneProxyAssign(BaseModel):
    proxy_id: str = Field(min_length=1)
