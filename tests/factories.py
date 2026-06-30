"""factory-boy factories for the domain objects built across the test suite.

Each factory declares the model's *required* fields with canonical defaults, so
``XFactory.build(**overrides)`` is equivalent to constructing the model directly
with the same overrides — it only fills the boilerplate required fields, leaving
every optional field at the model's own default. That keeps existing assertions
valid while removing repetitive multi-field construction (notably the 7-field
``DeviceFingerprint``).
"""

from __future__ import annotations

import factory

from core.db import assign_account_to_proxy, create_proxy
from schemas.accounts import AccountCreate, AccountRead
from schemas.device_fingerprint import DeviceFingerprint
from schemas.proxy import ProxyCreate

_TS = "2024-01-01T00:00:00+00:00"


class AccountCreateFactory(factory.Factory):
    class Meta:
        model = AccountCreate

    account_id = factory.Sequence(lambda n: f"acc-{n}")


class AccountReadFactory(factory.Factory):
    class Meta:
        model = AccountRead

    account_id = factory.Sequence(lambda n: f"acc-{n}")
    status = "new"
    created_at = _TS
    updated_at = _TS


class DeviceFingerprintFactory(factory.Factory):
    class Meta:
        model = DeviceFingerprint

    account_id = factory.Sequence(lambda n: f"acc-{n}")
    platform = "windows"
    device_model = "Desktop"
    system_version = "Windows 11"
    app_version = "5.4.0 x64"
    lang_code = "en"
    system_lang_code = "en-US"


class ProxyCreateFactory(factory.Factory):
    class Meta:
        model = ProxyCreate

    proxy_type = "socks5"
    host = "127.0.0.1"
    port = 1080


async def seed_account_proxy(
    account_id: str,
    *,
    host: str = "127.0.0.1",
    port: int = 1080,
    username: str | None = None,
    password: str | None = None,
) -> str:
    """Create a SOCKS5 pool proxy and assign it to an account; return the proxy id.

    The pool replaced the per-account 1:1 proxy, so tests that just need "this
    account has a proxy" use this seam instead of a single upsert.
    """
    proxy = await create_proxy(
        ProxyCreate(
            proxy_type="socks5",
            host=host,
            port=port,
            username=username,
            password=password,
        ),
    )
    await assign_account_to_proxy(proxy.id, account_id)
    return proxy.id
