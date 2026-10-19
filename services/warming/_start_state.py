"""Persisted stint fields carried across a warming restart."""

from __future__ import annotations

from typing import TYPE_CHECKING, NamedTuple

from core.config import settings
from core.logging import log_event
from core.telegram_client import mobile_api_configured
from schemas.lifecycle import LifecycleSnapshot
from schemas.warming import is_warming
from services.lifecycle_journal import record_lifecycle_event
from services.warming._start_options import first_run_at
from services.warming._waves import next_wave
from services.warming.pacing import _now_iso

if TYPE_CHECKING:
    from schemas.accounts import AccountRead
    from schemas.device_fingerprint import DeviceType
    from schemas.devices import DeviceRefusalCode
    from schemas.warming import ActivityPersona, StartWarmingRequest, WarmingStateRecord


_DEVICE_MISSING: DeviceRefusalCode = "device_missing"
_NO_MOBILE_API: DeviceRefusalCode = "mobile_api_not_configured"
_NO_EMULATOR: DeviceRefusalCode = "mobile_emulator_disabled"


class WarmingDeviceUnavailableError(ValueError):
    """The stint's device cannot warm: the account lacks it, or mobile is not set up.

    Never answered by warming the account's other device instead.
    """

    def __init__(self, code: DeviceRefusalCode, account_id: str) -> None:
        self.code: DeviceRefusalCode = code
        super().__init__(f"{code}: {account_id}")


class NotGraduatedError(ValueError):
    """«Вернуть в прогрев» on an account that is not on the warmed card (anymore)."""


def assert_on_warmed_card(existing: WarmingStateRecord | None, account_id: str) -> None:
    """Refuse «Вернуть в прогрев» unless the account is graduated and not handed off.

    Checked under the account lock, so a stale click cannot restart an account someone
    already moved on; a graduated account is idle, so the start is a fresh wave.
    """
    if existing is None or not existing.promoted_to_nc or existing.nc_handed_off:
        raise NotGraduatedError(account_id)


def _assert_device_ready(device_mode: DeviceType, account: AccountRead) -> None:
    """Refuse a stint whose device cannot warm; never swap in the other one."""
    if device_mode not in account.devices:
        raise WarmingDeviceUnavailableError(_DEVICE_MISSING, account.account_id)
    if device_mode == "mobile" and not mobile_api_configured():
        raise WarmingDeviceUnavailableError(_NO_MOBILE_API, account.account_id)
    # A phone stint visits through the emulator only; Telethon never stands in for it.
    if device_mode == "mobile" and not settings.emulator.enabled:
        raise WarmingDeviceUnavailableError(_NO_EMULATOR, account.account_id)


class CarriedStint(NamedTuple):
    """Stable fields that one warming start writes to the account row."""

    started_at: str
    target_days: int
    activity_persona: ActivityPersona
    device_mode: DeviceType
    # The chosen first cycle (``None`` → the loop's cold-start spread).
    first_run_at: str | None
    # A start from idle (or a first start) opens a new stint — the next wave — whose
    # worked days begin at zero; a restart of a running stint keeps them and its wave.
    fresh: bool
    wave: int


def carry_or_restamp(
    existing: WarmingStateRecord | None,
    data: StartWarmingRequest,
    account: AccountRead,
) -> CarriedStint:
    """Carry an in-flight stint; restamp a genuine start from idle/stopped as a new wave.

    A genuine start with a chosen delay begins its stint at the first cycle, not at
    the click: the rest before it must not count toward ``target_days``, or a 1-day
    target behind a 24-hour delay would complete before warming did anything.
    Refuses (``WarmingDeviceUnavailableError``) a stint whose device ``account``
    cannot warm through, before anything about the running stint changes.

    A graduated account («Вернуть в прогрев») is idle, so it starts a new wave from
    zero like any other start from idle. So does a stopped spam-blocked account: its
    stint ended with the stop, it only waits on the board for the operator's decision.
    """
    continuing = (
        existing is not None and is_warming(existing.state) and existing.state != "spam_blocked"
    )
    first_run = first_run_at(account, data)
    restamp = first_run or _now_iso()
    carried = continuing
    started_at = existing.started_at if carried and existing and existing.started_at else restamp
    target_days = (
        existing.target_days
        if continuing and existing and existing.target_days
        else (data.target_days or settings.neurocomment.warmed_min_days)
    )
    persona = existing.activity_persona if continuing and existing else data.activity_persona
    device_mode = existing.device_mode if continuing and existing else data.device_mode
    _assert_device_ready(device_mode, account)
    wave = existing.wave if carried and existing else next_wave(existing)
    return CarriedStint(
        started_at, target_days, persona, device_mode, first_run, fresh=not carried, wave=wave
    )


async def journal_start(
    account_id: str,
    existing: WarmingStateRecord | None,
    stint: CarriedStint,
    account: AccountRead,
) -> None:
    """Log and journal a start; ``new_stint`` False = a restart that carried the stint's clock.

    The row is pinned to this start's own stint, not re-read from state: it runs after the
    account lock is released, when a concurrent stop/restart may have stamped a newer one.
    It also keeps what the survival cohorts group by — persona, length, proxy country —
    so they stay readable after the account is gone.
    """
    await log_event("INFO", "warming_started", account_id=account_id)
    new_stint = existing is None or existing.started_at != stint.started_at
    snapshot = LifecycleSnapshot(tg_user_id=account.user_id, stint_started_at=stint.started_at)
    extra: dict[str, object] = {
        "new_stint": new_stint,
        "persona": stint.activity_persona,
        "target_days": stint.target_days,
        "wave": stint.wave,
        "proxy_country": (account.proxy_country_code or "").upper() or None,
    }
    await record_lifecycle_event(account_id, "warming_started", extra, snapshot)
