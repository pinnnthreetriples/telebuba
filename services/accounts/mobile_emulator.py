"""Open a mobile account in its local Android pod (redroid under Docker).

The pod is the account's MOBILE device made visible: its Android container is created
with the mobile fingerprint's manufacturer and model, refused when its Android SDK
differs, and lives in the network namespace of a gateway container that sends all of
its traffic through the account's SOCKS5 proxy, or none (``core.emulator.pod``).
Android itself knows nothing of the proxy: no VPN, no in-app setting. The I/O lives in
``core.emulator``; the policy lives here:

* refuse what cannot work before any container is touched — the feature switched
  off, no mobile device, no proxy or one the tunnel cannot use, or a stored 2FA
  password ``input text`` cannot type (found out at the 2FA screen, it would cost a
  login code already sent);
* one start at a time per account (a second one answers ``emulator_busy``);
* the container is a real phone's firmware with the proxy country's timezone and
  carrier (the phone number's country while the proxy has none on record); a
  stopped container gets them rewritten before it boots, as the proxy may have moved;
* every start gives the gateway the account's CURRENT proxy before Android runs;
* a running pod is used as it stands only while its gateway runs the current proxy;
  one on another proxy (or whose gateway died) is stopped and started again, and a
  proxy change stops the account's pod at once (``stop_account_pod``);
* a pod that is not running is started by ``_mobile_start``: a new container's APK
  fetched alongside its boot, its first boot seeded, and a failure's clean-up;
* the sign-in state is read off the screen every time, never remembered;
* an app that shows its intro or phone screen is signed in where it stands, by the
  operator's open and warming's alike (a visit heals an app that fell signed out);
  one showing the chat list, or anything else, is left alone;
* on any failure the pod this call started is stopped again, and removed when this
  call created it and it failed by its boot (a booted one keeps its seeded data); the
  operator's sign-in that stops short is no failure: its pod stays up for the hand;
* every refusal is logged (``account_mobile_open_failed``), and each stage the open
  enters is reported for the operator's dialog (``open_mobile_progress``);
* an activity session (``run_mobile_activity``) uses a running, signed-in pod as it
  stands and never starts one; it ends with what it did when its time is up, and a
  failed one carries what it did on the refusal (``partial``). A warming visit opens
  with ``open_mobile_visit``, whose serial spares its session checking the pod again;
* a background open (warming's) shows no window, and leaves a pod the operator opened
  alone (``operator_holds``); the operator's open is ``emulator_busy`` while a session runs;
* a window that cannot open (no scrcpy) is a warning, never a failed open;
* an operator holding a phone's controls on the web screen (``claim_manual``) excludes
  an open and an activity session, and they exclude it: the second is ``emulator_busy``.

The per-account locks are process-local, like the rest of the runtime state (one
uvicorn worker). A start takes the lock before it reads the account, and only the
account's deletion drops it: a start never does, because a deletion queued on that
same lock would otherwise share the account with the next start's fresh lock.
"""

from __future__ import annotations

import asyncio
from typing import TYPE_CHECKING

from core.config import settings
from core.db import (
    fetch_account_proxy_settings,
    fetch_account_twofa_password,
    fetch_device_fingerprint,
)
from core.emulator import (
    EmulatorError,
    TelegramMobilePlaybook,
    adb_serial,
    bring_app_to_front,
    connect_device,
    container_spec,
    escape_input_text,
    find_instance,
    leave_app,
    pod_runs_proxy,
    read_screen,
    remove_pod,
    stop_pod,
    unpause_pod,
    viewer_open,
)
from core.logging import log_event
from core.phone_geo import country_for_phone
from schemas.emulator import OpenMobileResult
from services.accounts._mobile_holds import (
    _ACTIVE,
    _LOCKS,
    _MANUAL,
    _OPERATOR,
    EmulatorBusyError,
    EmulatorRefusedError,
    claim_manual,
    container_name,
    phone_activity,
    release_manual,
)
from services.accounts._mobile_progress import forget_progress, open_progress, report_stage
from services.accounts._mobile_start import (
    ENTRY_SCREENS,
    set_battery,
    show,
    sign_in_or_hand_over,
    start_and_sign_in,
)
from services.accounts._result import AccountActionError
from services.accounts.devices import DeviceRefusedError
from services.accounts.lifecycle import require_account

if TYPE_CHECKING:
    from schemas.accounts import AccountRead
    from schemas.device_fingerprint import DeviceFingerprint
    from schemas.devices import DeviceRefusalCode
    from schemas.emulator import (
        EmulatorRefusalCode,
        MobileOpenProgress,
        PlaybookPlan,
        PlaybookResult,
    )
    from schemas.proxy import ProxySettings

_DEVICE_MISSING: DeviceRefusalCode = "device_missing"
_EMULATOR_NOT_RUNNING: EmulatorRefusalCode = "emulator_not_running"
_EMULATOR_PROXY_MISSING: EmulatorRefusalCode = "emulator_proxy_missing"
_EMULATOR_PROXY_UNSUPPORTED: EmulatorRefusalCode = "emulator_proxy_unsupported"
_MOBILE_EMULATOR_DISABLED: EmulatorRefusalCode = "mobile_emulator_disabled"

__all__ = [
    "EmulatorBusyError",
    "EmulatorRefusedError",
    "claim_manual",
    "container_name",
    "open_account_mobile",
    "open_mobile_progress",
    "open_mobile_visit",
    "operator_holds",
    "phone_activity",
    "release_manual",
    "remove_account_container",
    "run_mobile_activity",
    "running_serial",
    "stop_account_pod",
]

# A session gets this long past its own length to finish its last read before it is cut.
_SESSION_GRACE_SECONDS = 60


async def running_serial(account_id: str) -> str:
    """The adb serial of the account's running pod; ``emulator_not_running`` otherwise.

    Read-only: the pod is neither started nor checked against the account's proxy.
    """
    try:
        instance = await find_instance(container_name(account_id))
    except EmulatorError as exc:
        raise EmulatorRefusedError(exc.code) from exc
    if instance is None or not instance.running:
        raise EmulatorRefusedError(_EMULATOR_NOT_RUNNING)
    return adb_serial(instance.index)


async def _refuse_what_cannot_work(
    account: AccountRead,
) -> tuple[DeviceFingerprint, ProxySettings]:
    """The mobile fingerprint and proxy to build the pod from, or why there are none."""
    if not settings.emulator.enabled:
        raise EmulatorRefusedError(_MOBILE_EMULATOR_DISABLED)
    if "mobile" not in account.devices:
        raise DeviceRefusedError(_DEVICE_MISSING)
    if account.proxy_id is None:
        raise EmulatorRefusedError(_EMULATOR_PROXY_MISSING)
    if account.proxy_type != "socks5":
        raise EmulatorRefusedError(_EMULATOR_PROXY_UNSUPPORTED)
    proxy = await fetch_account_proxy_settings(account.account_id)
    if proxy is None:
        raise EmulatorRefusedError(_EMULATOR_PROXY_MISSING)
    fingerprint = await fetch_device_fingerprint(account.account_id, "mobile")
    if fingerprint is None:
        raise DeviceRefusedError(_DEVICE_MISSING)
    password = await fetch_account_twofa_password(account.account_id)
    if password:
        try:
            escape_input_text(password)
        except EmulatorError as exc:
            raise EmulatorRefusedError(exc.code) from exc
    return fingerprint, proxy


def operator_holds(account_id: str) -> bool:
    """Whether the pod is the operator's: opened by them, its window open while there are any.

    With no desktop window it stays theirs until they stop it or end their control of it.
    """
    if account_id not in _OPERATOR:
        return False
    if not settings.emulator.desktop_viewer or viewer_open(container_name(account_id)):
        return True
    _OPERATOR.discard(account_id)
    return False


async def open_account_mobile(account_id: str, *, background: bool = False) -> OpenMobileResult:
    """Start (or find running) the account's container and sign its Telegram app in.

    ``background`` (warming) opens no window and signs the app in only when it shows
    an entry screen; ``signed_in`` is what the screen shows at the end. It is busy
    while the operator holds the pod; the operator's open is busy while an activity
    session runs.
    """
    return (await _open_reported(account_id, background=background))[0]


async def open_mobile_visit(account_id: str) -> str | None:
    """Warming's open (``background``): the serial of the pod it left signed in, else ``None``.

    The visit hands it straight to ``run_mobile_activity``: this open has just read the
    account, matched the gateway's proxy, connected adb and put the app in front, so
    the session does not do it all again (two DB passes, a ``docker ps``, a
    ``docker exec sha256sum``, ``adb connect``, ``pm list packages``, ``monkey``).
    """
    result, serial = await _open_reported(account_id, background=True)
    return serial if result.signed_in else None


async def _open_reported(account_id: str, *, background: bool) -> tuple[OpenMobileResult, str]:
    """``open_account_mobile``'s open, with the pod's serial; refusals logged and reported."""
    lock = _LOCKS.setdefault(account_id, asyncio.Lock())
    held = operator_holds(account_id) if background else account_id in _ACTIVE
    # An operator's hands on the phone: an open would pull the app to the front, sign
    # in over them or restart the pod (and a warming cycle would then stop it).
    if lock.locked() or held or account_id in _MANUAL:
        raise EmulatorBusyError
    report_stage(account_id, "pod")
    try:
        async with lock:
            account = await require_account(account_id)
            fingerprint, proxy = await _refuse_what_cannot_work(account)
            result, serial = await _open(account, fingerprint, proxy, background=background)
    except (EmulatorError, AccountActionError) as exc:
        report_stage(account_id, "failed", exc.code)
        await log_event(
            "WARNING",
            "account_mobile_open_failed",
            account_id=account_id,
            extra={"reason": exc.code},
        )
        if isinstance(exc, EmulatorError):
            raise EmulatorRefusedError(exc.code) from exc
        raise
    except BaseException:
        report_stage(account_id, "failed")
        raise
    if result.signed_in:
        report_stage(account_id, "done")
    else:
        report_stage(account_id, "manual", result.sign_in_refusal)
    if not background:
        _OPERATOR.add(account_id)
    await log_event("INFO", "account_mobile_opened", account_id=account_id)
    return result, serial


def open_mobile_progress(account_id: str) -> MobileOpenProgress:
    """Where the account's latest open is (``_mobile_progress``)."""
    return open_progress(account_id)


async def _open(
    account: AccountRead,
    fingerprint: DeviceFingerprint,
    proxy: ProxySettings,
    *,
    background: bool,
) -> tuple[OpenMobileResult, str]:
    spec = container_spec(
        fingerprint, account.proxy_country_code or country_for_phone(account.phone)
    )
    name = container_name(account.account_id)
    instance = await find_instance(name)
    if instance is not None and instance.paused:
        # Parked after a close visit: thawed in a moment, then checked like a running one.
        await unpause_pod(name)
        instance = instance._replace(running=True, paused=False)
    if instance is not None and instance.running:
        if await pod_runs_proxy(name, proxy):
            # Up already and on the current proxy: not booted or reconfigured again.
            # Only an entry screen is acted on, through its nodes.
            serial = adb_serial(instance.index)
            report_stage(account.account_id, "app")
            await connect_device(serial)
            await set_battery(serial, account.account_id, spec.region.timezone)
            if not background:
                await show(name, instance.index)
            await bring_app_to_front(serial)
            screen, _nodes = await read_screen(serial)
            signed_in, refusal = screen == "chats", None
            if screen in ENTRY_SCREENS:
                signed_in, refusal = await sign_in_or_hand_over(
                    account, serial, background=background
                )
            return OpenMobileResult(
                account_id=account.account_id,
                status="already_running",
                signed_in=signed_in,
                sign_in_refusal=refusal,
            ), serial
        await stop_pod(name)
    return await start_and_sign_in(
        account, spec, proxy, new=instance is None, background=background
    )


async def run_mobile_activity(
    account_id: str,
    duration_sec: int,
    plan: PlaybookPlan | None = None,
    *,
    opened: str | None = None,
) -> PlaybookResult:
    """Spend up to ``duration_sec`` (capped) in the account's running Telegram app.

    ``plan`` shapes the session (channels, reactions, stories, an action cap); none is
    the playbook's default. A finished session leaves the app with HOME and gives it a
    poll interval to go offline, as a phone put away does, before the pod is stopped.

    The pod is used as it stands: one that is down, or whose gateway runs another
    proxy, answers ``emulator_not_running``. The session does not hold the account's
    lock, so a proxy change or a deletion stops the pod at once and the session fails
    on its next command; the refusal carries what it did (``partial``). One session per
    account, none while a start is in flight or the operator holds the pod. A session
    still going ``_SESSION_GRACE_SECONDS`` past its length ends there, with what it did.

    ``opened`` is the serial ``open_mobile_visit`` just gave the same visit: the pod is
    then used as that open left it, unchecked (a proxy change or deletion since stopped
    it, and the session fails on its first command like one cut off later). Who holds
    the pod is still asked: those are this process's own sets, no I/O.
    """
    if phone_activity(account_id) is not None or operator_holds(account_id):
        raise EmulatorBusyError
    _ACTIVE.add(account_id)
    playbook: TelegramMobilePlaybook | None = None
    try:
        serial = opened or await _serial_on_current_proxy(account_id)
        seconds = min(duration_sec, settings.emulator.activity_max_seconds)
        playbook = TelegramMobilePlaybook()
        result = await _run_session(playbook, serial, seconds, plan)
        await leave_app(serial)
        await asyncio.sleep(settings.emulator.poll_seconds)
    except EmulatorError as exc:
        await log_event(
            "WARNING",
            "account_mobile_activity_failed",
            account_id=account_id,
            extra={"reason": exc.code},
        )
        partial = playbook.result if playbook is not None else None
        raise EmulatorRefusedError(exc.code, partial=partial) from exc
    finally:
        _ACTIVE.discard(account_id)
    await log_event(
        "INFO",
        "account_mobile_activity_done",
        account_id=account_id,
        extra=result.model_dump(mode="json"),
    )
    return result


async def _serial_on_current_proxy(account_id: str) -> str:
    """The running pod's serial, its app in front; ``emulator_not_running`` off the proxy."""
    account = await require_account(account_id)
    _fingerprint, proxy = await _refuse_what_cannot_work(account)
    name = container_name(account_id)
    instance = await find_instance(name)
    if instance is None or not instance.running or not await pod_runs_proxy(name, proxy):
        raise EmulatorRefusedError(_EMULATOR_NOT_RUNNING)
    serial = adb_serial(instance.index)
    await connect_device(serial)
    await bring_app_to_front(serial)
    return serial


async def _run_session(
    playbook: TelegramMobilePlaybook, serial: str, seconds: int, plan: PlaybookPlan | None
) -> PlaybookResult:
    """The playbook's session, cut ``_SESSION_GRACE_SECONDS`` past its length."""
    timeout = asyncio.timeout(seconds + _SESSION_GRACE_SECONDS)
    try:
        async with timeout:
            return await playbook.run_session(serial, seconds, plan)
    except TimeoutError:
        if not timeout.expired():
            raise
    return playbook.result


async def stop_account_pod(account_id: str) -> None:
    """Stop the account's pod after its proxy changed; never fails the proxy change.

    Waits for a start in flight, which may have read the old proxy. The next open
    starts the pod on the new one; ``_open`` also restarts a running pod whose gateway
    runs another proxy, which covers a change made while this process was down.
    """
    if not settings.emulator.enabled:
        return
    lock = _LOCKS.setdefault(account_id, asyncio.Lock())
    _OPERATOR.discard(account_id)
    try:
        async with lock:
            await stop_pod(container_name(account_id))
    except EmulatorError as exc:
        await log_event(
            "WARNING",
            "account_mobile_pod_stop_failed",
            account_id=account_id,
            extra={"reason": exc.code},
        )


async def remove_account_container(account_id: str) -> None:
    """Delete a removed account's pod, if it has one; never blocks the deletion.

    Waits for a start of the same account in flight, so that start cannot create a
    pod after this one is gone. It alone drops the account's lock: the account
    row is already deleted, so a later start finds no account under a fresh lock.
    """
    lock = _LOCKS.setdefault(account_id, asyncio.Lock())
    _OPERATOR.discard(account_id)
    forget_progress(account_id)
    try:
        async with lock:
            removed = await remove_pod(container_name(account_id))
    except EmulatorError as exc:
        await log_event(
            "WARNING",
            "account_mobile_container_remove_failed",
            account_id=account_id,
            extra={"reason": exc.code},
        )
        return
    finally:
        if _LOCKS.get(account_id) is lock:
            del _LOCKS[account_id]
    if removed:
        await log_event("INFO", "account_mobile_container_removed", account_id=account_id)
