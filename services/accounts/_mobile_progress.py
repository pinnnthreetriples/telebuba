"""Where each account's latest pod open is, for the operator watching it start.

The open reports each stage as it enters it (``schemas.emulator.MobileOpenStage``);
the SPA polls ``open_progress`` while its phone dialog is open, so the operator sees
the phone boot and the app being signed in instead of a bare spinner. Only the latest
open per account is kept, and only stable codes: never a phone number or a login code.
Process-local, like the rest of the runtime state (one uvicorn worker).
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from schemas.emulator import MobileOpenProgress
from services.accounts._mobile_holds import container_name

if TYPE_CHECKING:
    from schemas.emulator import MobileOpenStage

_PROGRESS: dict[str, tuple[MobileOpenStage, str | None]] = {}


def report_stage(account_id: str, stage: MobileOpenStage, reason: str | None = None) -> None:
    """Record that the account's open entered ``stage``; ``reason`` is a refusal code."""
    _PROGRESS[account_id] = (stage, reason)


def forget_progress(account_id: str) -> None:
    _PROGRESS.pop(account_id, None)


def open_progress(account_id: str) -> MobileOpenProgress:
    """The account's latest open; ``stage`` ``None`` when this process saw none."""
    stage, reason = _PROGRESS.get(account_id, (None, None))
    return MobileOpenProgress(
        account_id=account_id,
        phone_id=container_name(account_id),
        stage=stage,
        reason=reason,
    )
