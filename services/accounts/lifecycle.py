"""Account lifecycle — registration and geo evaluation."""

from __future__ import annotations

from typing import TYPE_CHECKING

from core.db import (
    create_account,
    fetch_account,
    fetch_device_fingerprint,
    list_accounts,
)
from core.device_fingerprint import get_or_create_device_fingerprint
from core.logging import log_event
from core.phone_geo import evaluate_geo
from schemas.geo import GeoMatch

if TYPE_CHECKING:
    from schemas.accounts import AccountCreate, AccountRead


async def add_account(data: AccountCreate) -> AccountRead:
    account = await create_account(data)
    await get_or_create_device_fingerprint(account.account_id)
    saved = await list_accounts()
    persisted = next(
        (item for item in saved.accounts if item.account_id == account.account_id),
        account,
    )
    await log_event(
        "INFO",
        "account_added",
        account_id=persisted.account_id,
        extra={"session_name": persisted.session_name},
    )
    return persisted


async def evaluate_account_geo(account_id: str) -> GeoMatch:
    """Non-blocking geo check: does the account's proxy country match its number?

    Compares the phone number's country (via ``phonenumbers``) against the proxy
    exit country, plus the device language region. A mismatch is a warning + risk
    signal for the UI, never a hard block (product decision).
    """
    account = await fetch_account(account_id)
    if account is None:
        return GeoMatch(status="unknown", message="account not found")
    fingerprint = await fetch_device_fingerprint(account_id)
    lang_code = fingerprint.system_lang_code if fingerprint else None
    return evaluate_geo(
        phone=account.phone,
        proxy_country=account.proxy_country_code,
        lang_code=lang_code,
    )
