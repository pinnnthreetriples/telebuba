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
* a container's first boot gives it contacts and photos (``core.emulator._seeder``)
  before the app starts; a seeding that fails is logged, never fails the open;
* the sign-in state is read off the screen every time, never remembered;
* an app that shows its intro or phone screen is signed in where it stands, by the
  operator's open and warming's alike (a visit heals an app that fell signed out);
  one showing the chat list, or anything else, is left alone;
* on any failure the pod this call started is stopped again, and removed when this
  call created it;
* an activity session (``run_mobile_activity``) uses a running, signed-in pod as it
  stands and never starts one; it ends with what it did when its time is up, and a
  failed one carries what it did on the refusal (``partial``);
* a background open (warming's) shows no window, and leaves a pod the operator
  opened alone while its window is open; the operator's open answers
  ``emulator_busy`` while a session runs;
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
import logging
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
    assert_sdk,
    bring_app_to_front,
    connect_device,
    container_spec,
    ensure_app,
    escape_input_text,
    find_instance,
    leave_app,
    pod_runs_proxy,
    read_screen,
    remove_pod,
    seed_device_if_needed,
    show_instance,
    start_pod,
    stop_pod,
    unpause_pod,
    viewer_open,
    wait_for_boot,
    wait_screen,
)
from core.logging import log_event
from core.phone_geo import country_for_phone
from schemas.emulator import OpenMobileResult
from services.accounts._emulator_sign_in import sign_in
from services.accounts._mobile_holds import (
    _ACTIVE,
    _LOCKS,
    _MANUAL,
    EmulatorBusyError,
    EmulatorRefusedError,
    claim_manual,
    container_name,
    phone_activity,
    release_manual,
)
from services.accounts.devices import DeviceRefusedError
from services.accounts.lifecycle import require_account

if TYPE_CHECKING:
    from core.emulator import ContainerSpec
    from core.emulator.ui_dump import Screen
    from schemas.accounts import AccountRead
    from schemas.device_fingerprint import DeviceFingerprint
    from schemas.devices import DeviceRefusalCode
    from schemas.emulator import EmulatorRefusalCode, PlaybookPlan, PlaybookResult
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
    "phone_activity",
    "release_manual",
    "remove_account_container",
    "run_mobile_activity",
    "running_serial",
    "stop_account_pod",
]

# Stdlib sink, codes and step names only (see ``core.emulator._errors``).
logger = logging.getLogger(__name__)

# Accounts whose pod the operator opened; theirs while its window stays open.
_OPERATOR: set[str] = set()

# A running app on one of these still needs signing in; anything else is left as is.
_ENTRY_SCREENS: set[Screen] = {"intro", "phone"}
# A background open waits for the app to show one of these, then reads it as it is.
_KNOWN_SCREENS: set[Screen] = {"chats", "intro", "phone", "confirm", "code", "password"}
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


def _operator_holds(account_id: str) -> bool:
    """Whether the operator opened the account's pod and its window is still open."""
    if account_id in _OPERATOR and viewer_open(container_name(account_id)):
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
    lock = _LOCKS.setdefault(account_id, asyncio.Lock())
    held = _operator_holds(account_id) if background else account_id in _ACTIVE
    # An operator's hands on the phone: an open would pull the app to the front, sign
    # in over them or restart the pod (and a warming cycle would then stop it).
    if lock.locked() or held or account_id in _MANUAL:
        raise EmulatorBusyError
    try:
        async with lock:
            account = await require_account(account_id)
            fingerprint, proxy = await _refuse_what_cannot_work(account)
            result = await _open(account, fingerprint, proxy, background=background)
    except EmulatorError as exc:
        await log_event(
            "WARNING",
            "account_mobile_open_failed",
            account_id=account_id,
            extra={"reason": exc.code},
        )
        raise EmulatorRefusedError(exc.code) from exc
    if not background:
        _OPERATOR.add(account_id)
    await log_event("INFO", "account_mobile_opened", account_id=account_id)
    return result


async def _show(name: str, index: int) -> None:
    """Open the pod's window; one that cannot open is logged and the open goes on."""
    try:
        await show_instance(name, index)
    except EmulatorError as exc:
        logger.warning("pod window %s did not open (%s)", index, exc.code)


async def _open(
    account: AccountRead,
    fingerprint: DeviceFingerprint,
    proxy: ProxySettings,
    *,
    background: bool,
) -> OpenMobileResult:
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
            await connect_device(serial)
            if not background:
                await _show(name, instance.index)
            await bring_app_to_front(serial)
            screen, _nodes = await read_screen(serial)
            signed_in = screen == "chats"
            if screen in _ENTRY_SCREENS:
                signed_in = await sign_in(account, serial)
            return OpenMobileResult(
                account_id=account.account_id,
                status="already_running",
                signed_in=signed_in,
            )
        await stop_pod(name)
    created = instance is None
    instance = await start_pod(name, spec, proxy)  # undoes itself when it fails
    try:
        serial = adb_serial(instance.index)
        await wait_for_boot(serial)
        if not background:
            await _show(name, instance.index)
        await assert_sdk(serial, spec.sdk)
        seeding = asyncio.create_task(_seed(account.account_id, serial, spec))
        try:
            await ensure_app(serial)
            # In the background only an entry screen is signed in; in front, always.
            screen = (await wait_screen(serial, _KNOWN_SCREENS))[0] if background else None
            signed_in = screen == "chats"
            if screen is None or screen in _ENTRY_SCREENS:
                signed_in = await sign_in(account, serial)
            await seeding
        finally:
            seeding.cancel()
            await asyncio.gather(seeding, return_exceptions=True)
    except BaseException:
        await asyncio.shield(_undo(name, created=created))
        raise
    return OpenMobileResult(account_id=account.account_id, status="started", signed_in=signed_in)


async def run_mobile_activity(
    account_id: str, duration_sec: int, plan: PlaybookPlan | None = None
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
    """
    if phone_activity(account_id) is not None or _operator_holds(account_id):
        raise EmulatorBusyError
    _ACTIVE.add(account_id)
    playbook: TelegramMobilePlaybook | None = None
    try:
        account = await require_account(account_id)
        _fingerprint, proxy = await _refuse_what_cannot_work(account)
        name = container_name(account_id)
        instance = await find_instance(name)
        if instance is None or not instance.running or not await pod_runs_proxy(name, proxy):
            raise EmulatorRefusedError(_EMULATOR_NOT_RUNNING)
        serial = adb_serial(instance.index)
        await connect_device(serial)
        await bring_app_to_front(serial)
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


async def _seed(account_id: str, serial: str, spec: ContainerSpec) -> None:
    """Give a never-seeded container its contacts and photos; never fails the open.

    Runs alongside the app's install and sign-in (they read no contacts). Anything but
    a cancellation is logged and the sign-in goes on; with no marker, the next boot retries.
    """
    try:
        await seed_device_if_needed(serial, spec)
    except Exception as exc:  # noqa: BLE001 - seeding must never fail the open
        reason = exc.code if isinstance(exc, EmulatorError) else type(exc).__name__
        await log_event(
            "WARNING",
            "account_mobile_seed_failed",
            account_id=account_id,
            extra={"reason": reason},
        )


async def _undo(name: str, *, created: bool) -> None:
    """Stop the pod this start booted; remove it when this start created it."""
    try:
        if created:
            await remove_pod(name)
        else:
            await stop_pod(name)
    except EmulatorError as exc:
        logger.warning("pod cleanup for %s failed (%s)", name, exc.code)


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
