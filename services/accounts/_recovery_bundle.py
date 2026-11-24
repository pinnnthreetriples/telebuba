"""The recovery bundle: what a ``.session`` copy cannot bring back, gathered for the vault.

A bare key does not revive an account: the device fingerprint (and the API id the key was
made with), the proxy binding and the cloud password live only in the database. This builds
one JSON document of them for ``core.session_vault`` to encrypt next to each session copy and
into a deleted account's tombstone. The proxy's username and password are NOT included; the
passkey's private key lives in the key vault and is not exportable, so only identifiers are.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime
from typing import TYPE_CHECKING

from core.db import fetch_account_twofa_pair, fetch_device_fingerprint, fetch_proxy
from core.repositories._accounts_passkey import fetch_account_passkey

if TYPE_CHECKING:
    from schemas.accounts import AccountRead

__all__ = ["BUNDLE_FORMAT", "build_recovery_bundle"]

BUNDLE_FORMAT = 1


async def build_recovery_bundle(account: AccountRead, *, deleted: bool = False) -> bytes:
    """The bundle as JSON bytes (the vault encrypts it); ``deleted`` adds ``deleted_at``."""
    fingerprints = [
        fingerprint.model_dump()
        for device in account.devices
        if (fingerprint := await fetch_device_fingerprint(account.account_id, device)) is not None
    ]
    proxy = await fetch_proxy(account.proxy_id) if account.proxy_id else None
    current, pending = await fetch_account_twofa_pair(account.account_id)
    passkey = await fetch_account_passkey(account.account_id)
    now = datetime.now(UTC).isoformat()
    bundle: dict[str, object] = {
        "format": BUNDLE_FORMAT,
        "written_at": now,
        "account_id": account.account_id,
        "session_name": account.session_name,
        "second_session_name": account.second_session_name,
        "phone": account.phone,
        "user_id": account.user_id,
        "device_fingerprints": fingerprints,
        "proxy_id": account.proxy_id,
        "proxy": (
            None
            if proxy is None
            else {"proxy_type": proxy.proxy_type, "host": proxy.host, "port": proxy.port}
        ),
        "cloud_password": current,
        "pending_cloud_password": pending,
        "passkey": None if passkey is None else passkey.model_dump(),
    }
    if deleted:
        bundle["deleted_at"] = now
    return json.dumps(bundle, sort_keys=True).encode()
