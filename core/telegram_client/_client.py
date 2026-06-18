"""Telethon client construction + lifecycle — the only place clients are built."""

from __future__ import annotations

from contextlib import asynccontextmanager
from typing import TYPE_CHECKING

from anyio import Path
from telethon import TelegramClient

from core.config import settings
from core.db import fetch_account_proxy_settings
from core.device_fingerprint import get_or_create_device_fingerprint
from schemas.device_fingerprint import TelegramClientProfile, TelegramClientRequest

if TYPE_CHECKING:
    from collections.abc import AsyncIterator

    from schemas.telegram_session import TelegramSessionCheckRequest


def _session_path(request: TelegramClientRequest) -> str:
    session_name = request.session_name or request.account_id
    return str(settings.telegram.session_dir / session_name)


async def prepare_telegram_client_profile(
    request: TelegramClientRequest,
) -> TelegramClientProfile:
    await _ensure_session_dir()
    device = await get_or_create_device_fingerprint(request.account_id)
    proxy = await fetch_account_proxy_settings(request.account_id)
    return TelegramClientProfile(
        account_id=request.account_id,
        session_path=_session_path(request),
        receive_updates=request.receive_updates,
        device=device,
        proxy_type=proxy.proxy_type if proxy else None,
        proxy_host=proxy.host if proxy else None,
        proxy_port=proxy.port if proxy else None,
        proxy_username=proxy.username if proxy else None,
        proxy_password=proxy.password if proxy else None,
    )


async def prepare_session_check_profile(
    request: TelegramSessionCheckRequest,
) -> TelegramClientProfile:
    return await prepare_telegram_client_profile(
        TelegramClientRequest(
            account_id=request.account_id,
            session_name=request.session_name,
            receive_updates=False,
        ),
    )


async def _ensure_session_dir() -> None:
    await Path(settings.telegram.session_dir).mkdir(parents=True, exist_ok=True)


def create_telegram_client(profile: TelegramClientProfile) -> TelegramClient:
    device = profile.device
    proxy = _proxy_config(profile)
    if proxy is not None:
        return TelegramClient(
            profile.session_path,
            settings.telegram.api_id,
            settings.telegram.api_hash,
            device_model=device.device_model,
            system_version=device.system_version,
            app_version=device.app_version,
            lang_code=device.lang_code,
            system_lang_code=device.system_lang_code,
            receive_updates=profile.receive_updates,
            timeout=settings.telegram.timeout_seconds,
            connection_retries=settings.telegram.connection_retries,
            retry_delay=settings.telegram.retry_delay_seconds,
            request_retries=settings.telegram.request_retries,
            flood_sleep_threshold=settings.telegram.flood_sleep_threshold,
            proxy=proxy,
        )
    return TelegramClient(
        profile.session_path,
        settings.telegram.api_id,
        settings.telegram.api_hash,
        device_model=device.device_model,
        system_version=device.system_version,
        app_version=device.app_version,
        lang_code=device.lang_code,
        system_lang_code=device.system_lang_code,
        receive_updates=profile.receive_updates,
        timeout=settings.telegram.timeout_seconds,
        connection_retries=settings.telegram.connection_retries,
        retry_delay=settings.telegram.retry_delay_seconds,
        request_retries=settings.telegram.request_retries,
        flood_sleep_threshold=settings.telegram.flood_sleep_threshold,
    )


@asynccontextmanager
async def telegram_client(request: TelegramClientRequest) -> AsyncIterator[TelegramClient]:
    profile = await prepare_telegram_client_profile(request)
    client = create_telegram_client(profile)
    try:
        yield client
    finally:
        await client.disconnect()


def _proxy_config(profile: TelegramClientProfile) -> dict[str, object] | None:
    if not profile.proxy_type or not profile.proxy_host or profile.proxy_port is None:
        return None
    # Telethon's proxy dict speaks python-socks names: "socks5" / "http". Our
    # internal type uses "https" to match how proxy sellers advertise the
    # protocol — same underlying CONNECT tunnel, just relabelled at the edge.
    telethon_type = "http" if profile.proxy_type == "https" else profile.proxy_type
    return {
        "proxy_type": telethon_type,
        "addr": profile.proxy_host,
        "port": profile.proxy_port,
        "rdns": True,
        "username": profile.proxy_username,
        "password": profile.proxy_password,
    }
