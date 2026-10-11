"""Native mobile Android emulator service and Zero-Click authentication orchestrator."""

from __future__ import annotations

import asyncio
import contextlib
import logging
import time
from typing import TYPE_CHECKING

from core.config import settings
from core.db import fetch_account_proxy_settings, fetch_account_twofa_password
from core.emulator.adb import (
    KEYCODE_ENTER,
    ADBDriver,
    parse_element_center,
)
from core.emulator.config import (
    EmulatorLaunchError,
    EmulatorNotFoundError,
    NoProxyForMobileError,
    generate_device_profile,
    is_emulator_available,
)
from core.emulator.ldconsole import (
    create_instance,
    is_running,
    launch_instance,
    set_device_profile,
    set_resolution,
    set_resource_limits,
    stop_instance,
)
from core.emulator.session_capture import poll_for_login_code
from core.web_login import LocalProxyRelay
from schemas.mobile_emulator import OpenMobileResult
from services.accounts.lifecycle import require_account

if TYPE_CHECKING:
    from schemas.proxy import ProxySettings

logger = logging.getLogger(__name__)

# Mutex locking per account to prevent duplicate concurrent launches
_account_mobile_locks: dict[str, asyncio.Lock] = {}
_mobile_locks_guard = asyncio.Lock()

# Registry of active emulator relays and instance names for clean shutdown
_active_relays: dict[str, tuple[LocalProxyRelay, ProxySettings]] = {}
_active_relays_lock = asyncio.Lock()


async def _get_account_lock(account_id: str) -> asyncio.Lock:
    """Get or create the per-account mutex lock."""
    async with _mobile_locks_guard:
        lock = _account_mobile_locks.get(account_id)
        if lock is None:
            lock = asyncio.Lock()
            _account_mobile_locks[account_id] = lock
        return lock


async def _safe_dump(adb: ADBDriver) -> str:
    """Safely dump UI hierarchy, returning empty string on failure."""
    with contextlib.suppress(EmulatorLaunchError, OSError):
        return await adb.dump_ui_hierarchy()
    return ""


def _is_authenticated_chat_screen(dump: str | None) -> bool:
    """Positively confirm that Telegram is displaying an authenticated chat screen."""
    if not dump:
        return False
    dump_lower = dump.lower()
    unauth_markers = (
        "start messaging",
        "продолжить",
        "phone number",
        "номер телефона",
        "your phone",
        "check your phone",
        "two-step",
        "cloud password",
        "облачный пароль",
        "enter code",
        "введите код",
    )
    if any(m in dump_lower for m in unauth_markers):
        return False

    auth_markers = (
        "chats",
        "чаты",
        "search",
        "поиск",
        "new message",
        "новое сообщение",
        "open navigation drawer",
        "открыть панель навигации",
        "org.telegram.messenger:id/chat_list",
        "org.telegram.messenger:id/chats_container",
        "org.telegram.messenger:id/menu_search",
        "org.telegram.messenger:id/floating_button",
    )
    return any(m in dump_lower for m in auth_markers)


async def _start_messaging_and_enter_phone(
    initial_dump: str,
    phone: str,
    adb: ADBDriver,
) -> float:
    """Navigate start screen, type phone number, and submit. Returns timestamp before submit."""
    start_coords = (
        parse_element_center(initial_dump, "Start Messaging")
        or parse_element_center(initial_dump, "Продолжить")
        or (206, 800)
    )
    await adb.tap(start_coords[0], start_coords[1])
    await asyncio.sleep(2.0)

    start_time = time.time()
    dump = await _safe_dump(adb)
    phone_coords = (
        parse_element_center(dump, "Phone number")
        or parse_element_center(dump, "Номер телефона")
        or (206, 240)
    )
    await adb.tap(phone_coords[0], phone_coords[1])

    clean_phone = phone.lstrip("+")
    await adb.input_text(clean_phone)
    await asyncio.sleep(0.5)

    next_coords = (
        parse_element_center(dump, "Next")
        or parse_element_center(dump, "arrow")
        or parse_element_center(dump, "Продолжить")
    )
    if next_coords:
        await adb.tap(next_coords[0], next_coords[1])
    else:
        await adb.press_key(KEYCODE_ENTER)

    return start_time


async def _handle_twofa_if_needed(account_id: str, adb: ADBDriver) -> None:
    """Submit 2FA password if prompted."""
    dump = await _safe_dump(adb)
    prompt_keywords = ("password", "two-step", "пароль", "cloud password")
    if not any(kw in dump.lower() for kw in prompt_keywords):
        return

    password = await fetch_account_twofa_password(account_id)
    if not password:
        return

    pw_coords = (
        parse_element_center(dump, "Password") or parse_element_center(dump, "Пароль") or (206, 250)
    )
    await adb.tap(pw_coords[0], pw_coords[1])
    await adb.input_text(password)
    await asyncio.sleep(0.5)
    await adb.press_key(KEYCODE_ENTER)
    await asyncio.sleep(3.0)


async def _perform_zero_click_login(
    account_id: str,
    phone: str,
    adb: ADBDriver,
) -> bool:
    """Execute the automated login flow using ADB and 777000 code capture."""
    dump = await _safe_dump(adb)
    if _is_authenticated_chat_screen(dump):
        return True

    start_time = await _start_messaging_and_enter_phone(dump, phone, adb)

    code = await poll_for_login_code(
        account_id=account_id,
        since_timestamp=start_time,
        timeout_seconds=float(settings.emulator.code_timeout_seconds),
    )

    await asyncio.sleep(2.0)
    await adb.input_text(code)
    await asyncio.sleep(3.0)

    await _handle_twofa_if_needed(account_id, adb)

    final_dump = await _safe_dump(adb)
    return _is_authenticated_chat_screen(final_dump)


async def _setup_instance_hardware(account_id: str, instance_name: str) -> None:
    """Create and configure deterministic hardware profiles for the emulator instance."""
    hw = generate_device_profile(account_id)
    with contextlib.suppress(EmulatorLaunchError, OSError):
        await create_instance(instance_name)

    await set_device_profile(
        instance_name,
        model=hw["model"],
        imei=hw["imei"],
        android_id=hw["android_id"],
        mac=hw["mac"],
    )
    await set_resolution(instance_name, width=412, height=915, dpi=320)
    await set_resource_limits(instance_name, cpus=1, ram_mb=1536)


async def _register_proxy_relay(
    account_id: str,
    proxy: ProxySettings,
) -> tuple[LocalProxyRelay, int]:
    """Start and register a local proxy relay for the emulator."""
    relay = LocalProxyRelay(proxy)
    relay_port = await relay.start()

    async with _active_relays_lock:
        if account_id in _active_relays:
            old_relay, _ = _active_relays.pop(account_id)
            await old_relay.aclose()
        _active_relays[account_id] = (relay, proxy)

    return relay, relay_port


async def _cleanup_failed_launch(
    account_id: str,
    instance_name: str,
    relay: LocalProxyRelay,
) -> None:
    """Clean up proxy relay and stop emulator instance upon a failed launch or login."""
    async with _active_relays_lock:
        active = _active_relays.get(account_id)
        if active is not None and active[0] is relay:
            _active_relays.pop(account_id)
    with contextlib.suppress(OSError):
        await relay.aclose()
    with contextlib.suppress(EmulatorLaunchError, OSError):
        await stop_instance(instance_name)


async def open_account_mobile(account_id: str) -> OpenMobileResult:
    """Open (or raise) an Android emulator instance for the account and authenticate.

    Binds traffic to the account's assigned proxy, configures physical device profile,
    and runs Zero-Click login via ADB and Telethon 777000 code interception.
    """
    account = await require_account(account_id)
    if not account.phone:
        msg = f"Account {account_id} has no phone number configured for mobile login"
        raise ValueError(msg)

    proxy = await fetch_account_proxy_settings(account_id)
    if proxy is None:
        msg = f"Account {account_id} has no assigned proxy"
        raise NoProxyForMobileError(msg)

    instance_name = f"telebuba_{account_id}"

    async with await _get_account_lock(account_id):
        if not is_emulator_available():
            msg = "LDPlayer or ADB binary not located on host"
            raise EmulatorNotFoundError(msg)

        adb = ADBDriver(instance_name)
        running = await is_running(instance_name)
        if running:
            dump = await _safe_dump(adb)
            if _is_authenticated_chat_screen(dump):
                return OpenMobileResult(
                    launched=True,
                    signed_in=True,
                    instance_name=instance_name,
                    message="Emulator instance already running",
                )

        if not running:
            await _setup_instance_hardware(account_id, instance_name)

        relay, relay_port = await _register_proxy_relay(account_id, proxy)

        try:
            if not running:
                await launch_instance(instance_name)
                booted = await adb.wait_for_boot(
                    timeout_seconds=float(settings.emulator.boot_timeout_seconds),
                )
                if not booted:
                    logger.warning(
                        "Instance %s boot wait timed out; attempting to proceed",
                        instance_name,
                    )

            await adb.set_reverse_proxy(relay_port)
            await adb.start_app("org.telegram.messenger")
            await asyncio.sleep(3.0)

            signed_in = await _perform_zero_click_login(account_id, account.phone, adb)
        except BaseException:
            await _cleanup_failed_launch(account_id, instance_name, relay)
            raise

    return OpenMobileResult(
        launched=True,
        signed_in=signed_in,
        instance_name=instance_name,
    )


async def shutdown_mobile_emulator() -> None:
    """Gracefully terminate running emulators and close proxy relays on shutdown."""
    async with _active_relays_lock:
        active = list(_active_relays.items())
        _active_relays.clear()

    for account_id, (relay, _proxy) in active:
        instance_name = f"telebuba_{account_id}"
        with contextlib.suppress(EmulatorLaunchError, OSError):
            await stop_instance(instance_name)
        with contextlib.suppress(OSError):
            await relay.aclose()
