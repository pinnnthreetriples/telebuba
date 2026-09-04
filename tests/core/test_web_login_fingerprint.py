"""The per-account identity: deterministic, internally consistent, applied in both scopes.

Telegram records a web session from three values WebK reads off ``navigator``:
``userAgent``, ``platform`` and ``language``. It reads them inside its MTProto worker,
so the worker script is the one that must carry all three — that is what these tests
pin, alongside the device rows staying self-consistent (a macOS row never carrying a
Windows platform) and the same account always resolving to the same machine.
"""

from __future__ import annotations

import json
from typing import Any, cast

import pytest

from core.web_login.fingerprint import (
    DEVICES,
    _page_init_script,
    apply_page_fingerprint,
    fingerprint_for,
    worker_init_script,
)


class _Session:
    def __init__(self) -> None:
        self.commands: list[tuple[str, dict[str, object], str | None]] = []

    async def send_command(
        self,
        method: str,
        params: dict[str, object] | None = None,
        *,
        session_id: str | None = None,
    ) -> dict[str, object]:
        self.commands.append((method, params or {}, session_id))
        return {"result": {}}


def _config(script: str) -> dict[str, Any]:
    """Pull the injected ``__FP__`` literal back out of a generated script."""
    body = script.split("const C = ", 1)[1]
    return json.loads(body.split(";\n", 1)[0])


# ------------------------------------------------------------------------ resolution


def test_the_same_account_always_gets_the_same_device() -> None:
    first = fingerprint_for("acct-1", "DE")
    again = fingerprint_for("acct-1", "DE")
    assert first.device.name == again.device.name


def test_different_accounts_can_get_different_devices() -> None:
    names = {fingerprint_for(f"acct-{i}", None).device.name for i in range(40)}
    assert len(names) > 1


def test_country_drives_timezone_and_locale() -> None:
    fingerprint = fingerprint_for("acct-1", "de")  # lowercase is accepted
    assert fingerprint.timezone == "Europe/Berlin"
    assert fingerprint.locale == "de-DE"
    assert fingerprint.languages[0] == "de-DE"
    assert fingerprint.languages[-1] == "en"


def test_unknown_or_missing_country_falls_back_deterministically() -> None:
    unknown = fingerprint_for("acct-1", "ZZ")
    missing = fingerprint_for("acct-1", None)
    assert unknown.timezone == missing.timezone == "Etc/UTC"
    assert unknown.locale == missing.locale == "en-US"


def test_an_english_locale_does_not_repeat_english() -> None:
    fingerprint = fingerprint_for("acct-1", "GB")
    assert fingerprint.languages == ("en-GB", "en")


@pytest.mark.parametrize("device", DEVICES, ids=lambda d: d.name)
def test_every_device_row_is_self_consistent(device: Any) -> None:
    is_mac = device.nav_platform == "MacIntel"
    assert ("Macintosh" in device.user_agent) is is_mac
    assert (device.ua_platform == "macOS") is is_mac
    assert ("Edg/" in device.user_agent) is device.is_edge
    assert device.chrome_full.startswith(device.chrome_major)


# -------------------------------------------------------------------- worker identity


def test_the_worker_script_carries_all_three_values_telegram_records() -> None:
    fingerprint = fingerprint_for("acct-1", "DE")
    config = _config(worker_init_script(fingerprint))

    # device_model, system_version and system_lang_code, in WebK's own terms.
    assert config["userAgent"] == fingerprint.user_agent
    assert config["navPlatform"] == fingerprint.device.nav_platform
    assert config["locale"] == fingerprint.locale


def test_the_worker_script_overrides_the_navigator_surface() -> None:
    script = worker_init_script(fingerprint_for("acct-1", "DE"))
    for prop in ("userAgent", "platform", "language", "languages"):
        assert f"'{prop}'" in script
    # A worker has no screen and no WebGL context to patch.
    assert "screen" not in script


# ---------------------------------------------------------------------- page identity


@pytest.mark.asyncio
async def test_apply_page_fingerprint_targets_one_session_and_enables_page_first() -> None:
    session = _Session()
    fingerprint = fingerprint_for("acct-1", "DE")

    await apply_page_fingerprint(session, fingerprint, session_id="P1")  # ty: ignore[invalid-argument-type]

    methods = [method for method, _params, _target in session.commands]
    assert methods == [
        "Emulation.setUserAgentOverride",
        "Emulation.setTimezoneOverride",
        "Emulation.setLocaleOverride",
        # Without the domain enabled the document-start script is silently dropped.
        "Page.enable",
        "Page.addScriptToEvaluateOnNewDocument",
    ]
    assert all(target == "P1" for _m, _p, target in session.commands)

    ua = session.commands[0][1]
    assert ua["userAgent"] == fingerprint.user_agent
    assert ua["acceptLanguage"] == fingerprint.accept_language
    metadata = cast("dict[str, Any]", ua["userAgentMetadata"])
    assert metadata["platform"] == fingerprint.device.ua_platform
    brands = [brand["brand"] for brand in metadata["brands"]]
    expected_brand = "Microsoft Edge" if fingerprint.device.is_edge else "Google Chrome"
    assert expected_brand in brands

    timezone = session.commands[1][1]
    assert timezone["timezoneId"] == fingerprint.timezone


def test_the_page_script_hardens_what_the_worker_cannot_see() -> None:
    fingerprint = fingerprint_for("acct-1", "DE")

    config = _config(_page_init_script(fingerprint))
    assert config["screenW"] == fingerprint.device.screen_w
    assert config["webglRenderer"] == fingerprint.device.webgl_renderer
    # platform and languages are NOT repeated here: setUserAgentOverride already
    # settles both for the page, and a second copy would only drift.
    assert "navPlatform" not in config
    assert "languages" not in config
