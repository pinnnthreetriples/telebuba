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

from core.web_login import fingerprint as fp_module
from core.web_login._devices import BY_NAME, CATALOGUE, DEVICES
from core.web_login._scripts import QR_CAPTURE_HOOK
from core.web_login.browser import BrowserNotFoundError
from core.web_login.fingerprint import (
    _device_for,
    _page_init_script,
    _seed_index,
    _ua_metadata,
    apply_page_fingerprint,
    fingerprint_for,
    note_browser_version,
    note_installed_browser,
    ua_override_params,
    worker_init_script,
)

_SEEN_BUILD = "148.0.7778.217"

# navigator.deviceMemory is quantized and capped at 8 by the spec.
_DEVICE_MEMORY_VALUES = {1, 2, 4, 8}


@pytest.fixture(autouse=True)
def _pinned_browser(monkeypatch: pytest.MonkeyPatch) -> None:
    """The installed browser is process-wide state; never leak it between tests.

    Seeded rather than emptied so no test reaches for the host's own Chrome: an empty
    dict makes the next ``fingerprint_for`` scan Program Files for real.
    """
    monkeypatch.setattr(fp_module, "_observed", {"build": _SEEN_BUILD, "edge": ""})


def _fingerprint_with(device_name: str) -> Any:
    """A real fingerprint that resolved to the named device row.

    A row is only reachable on a host running that BRAND of browser, so the installed
    brand is pinned to the row's before seeding. Undone by ``_pinned_browser``.
    """
    row = next(device for device in DEVICES if device.name == device_name)
    note_installed_browser(fingerprint_for("acct-0").chrome_full, is_edge=row.is_edge)
    for i in range(500):
        candidate = fingerprint_for(f"acct-{i}", "DE")
        if candidate.device.name == device_name:
            return candidate
    msg = f"no account resolved to {device_name}"
    raise AssertionError(msg)


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


def test_an_unresolvable_country_claims_no_place_at_all() -> None:
    """A wrong zone is a contradiction anyone can compute; a zero offset is not.

    The caller resolves the exit country before asking for an identity, so this only
    fires when the proxy check itself could not answer. Naming a concrete zone then —
    America/New_York, say — positively claims a country the exit IP disagrees with,
    and timezone-versus-IP is the most routinely computed geo check there is.
    """
    unknown = fingerprint_for("acct-1", "ZZ")
    missing = fingerprint_for("acct-1", None)

    assert unknown.timezone == missing.timezone
    assert unknown.locale == missing.locale
    assert unknown.timezone == "Etc/UTC"
    # And it is never silently one of the real countries we hand out on purpose.
    assert unknown.timezone not in {zone for zone, _lang in fp_module._COUNTRY.values()}


def test_an_english_locale_does_not_repeat_english() -> None:
    fingerprint = fingerprint_for("acct-1", "GB")
    assert fingerprint.languages == ("en-GB", "en")


def test_accept_language_carries_bare_tags_without_q_values() -> None:
    """Chromium splits acceptLanguage on commas and does NOT strip q-values.

    A ``de-DE,de;q=0.9,en;q=0.8`` here lands verbatim in ``navigator.languages``, where
    no real browser has ever put a ``;q=`` — a one-expression detector.
    """
    fingerprint = fingerprint_for("acct-1", "DE")

    assert ";q=" not in fingerprint.accept_language
    assert ";" not in fingerprint.accept_language
    assert fingerprint.accept_language.split(",") == list(fingerprint.languages)


@pytest.mark.parametrize("device", DEVICES, ids=lambda d: d.name)
def test_every_device_row_is_self_consistent(device: Any) -> None:
    is_mac = device.nav_platform == "MacIntel"
    assert ("Macintosh" in device.ua_template) is is_mac
    assert (device.ua_platform == "macOS") is is_mac
    assert ("Edg/" in device.ua_template) is device.is_edge
    # An Apple-silicon row cannot report an x86 architecture to client hints, and an
    # x86 row cannot claim an Apple GPU.
    assert device.architecture in {"x86", "arm"}
    assert (device.architecture == "arm") is ("Apple M" in device.webgl_renderer)


@pytest.mark.parametrize("device", DEVICES, ids=lambda d: d.name)
def test_no_device_claims_an_impossible_device_memory(device: Any) -> None:
    """The Device Memory API is quantized and capped at 8; 16 exists on no browser."""
    assert device.device_memory in _DEVICE_MEMORY_VALUES


def test_the_edge_row_publishes_the_installed_edge_build() -> None:
    """An Edge row only ships on a host whose binary IS Edge, so the build is Edge's.

    Edge's ``Edg/`` token is its own build, never Chromium's — and on such a host the
    version read off the installation is exactly that. Synthesising one instead would
    put a made-up build on the token every site actually reads.
    """
    fingerprint = _fingerprint_with("win11-edge-intel")

    full = cast("list[dict[str, str]]", _ua_metadata(fingerprint)["fullVersionList"])
    edge_version = next(b["version"] for b in full if b["brand"] == "Microsoft Edge")
    assert edge_version == fingerprint.chrome_full
    assert fingerprint.edge_full == fingerprint.chrome_full


def test_client_hint_architecture_follows_the_device_row() -> None:
    apple = _ua_metadata(_fingerprint_with("macos-chrome-apple"))
    windows = _ua_metadata(_fingerprint_with("win11-chrome-intel"))

    assert apple["architecture"] == "arm"
    assert windows["architecture"] == "x86"


# ------------------------------------------------------------------- claimed version


def test_the_claimed_version_follows_the_real_browser() -> None:
    """JS feature detection is not overridden, so the milestone must be the real one."""
    note_browser_version("Chrome/148.0.7222.0")

    fingerprint = fingerprint_for("acct-1", "DE")
    assert fingerprint.chrome_full == "148.0.7222.0"
    assert fingerprint.chrome_major == "148"
    # The UA string carries the reduced form; the real build lives in fullVersionList.
    assert "Chrome/148.0.0.0" in fingerprint.user_agent
    brands = cast("list[dict[str, str]]", _ua_metadata(fingerprint)["brands"])
    assert {brand["version"] for brand in brands} == {"99", "148"}


def test_the_ua_string_is_reduced_the_way_every_real_chrome_reports_it() -> None:
    """Since UA reduction ``navigator.userAgent`` says ``Chrome/<major>.0.0.0``, always.

    A full build there is an anomaly on the single most-read string in the fingerprint:
    no genuine Chrome on earth emits one. Edge's ``Edg/`` token is reduced the same way.
    """
    note_browser_version("Chrome/148.0.7778.217")

    for name in ("win11-chrome-intel", "macos-chrome-apple", "win11-edge-intel"):
        ua = _fingerprint_with(name).user_agent
        assert "Chrome/148.0.0.0" in ua
        assert "148.0.7778.217" not in ua
    edge_ua = _fingerprint_with("win11-edge-intel").user_agent
    assert edge_ua.endswith("Edg/148.0.0.0")


def test_the_full_build_survives_where_client_hints_still_publish_it() -> None:
    """``fullVersionList`` is the surface that still carries the true build.

    Reducing it too would trade one contradiction for another: a browser whose hints
    claim ``148.0.0.0`` as a *full* version does not exist either.
    """
    note_browser_version("Chrome/148.0.7778.217")
    chrome = _fingerprint_with("win11-chrome-intel")
    edge = _fingerprint_with("win11-edge-intel")

    versions = {
        brand["brand"]: brand["version"]
        for brand in cast("list[dict[str, str]]", _ua_metadata(chrome)["fullVersionList"])
    }
    assert versions["Chromium"] == "148.0.7778.217"
    assert versions["Google Chrome"] == "148.0.7778.217"
    # And the Edge row still reports Edge's own build, not the Chromium one.
    edge_full = cast("list[dict[str, str]]", _ua_metadata(edge)["fullVersionList"])
    assert next(b["version"] for b in edge_full if b["brand"] == "Microsoft Edge") == edge.edge_full
    # "brands" stays the major-only list it always was.
    brands = cast("list[dict[str, str]]", _ua_metadata(chrome)["brands"])
    assert {brand["version"] for brand in brands} == {"99", "148"}


@pytest.mark.parametrize("field", [None, "", "Chrome/", "HeadlessChrome/not.a.version", 17])
def test_a_missing_or_junk_version_field_keeps_the_build_already_known(field: Any) -> None:
    before = fingerprint_for("acct-1", "DE").chrome_full

    note_browser_version(field)

    assert fingerprint_for("acct-1", "DE").chrome_full == before


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


@pytest.mark.parametrize(
    "script",
    [
        _page_init_script(fingerprint_for("acct-1", "DE")),
        worker_init_script(fingerprint_for("acct-1", "DE")),
    ],
    ids=["page", "worker"],
)
def test_overrides_land_on_the_prototype_not_the_instance(script: str) -> None:
    """Real Chrome keeps these accessors on the prototypes.

    ``Object.getOwnPropertyNames(navigator).length`` is 0 on a stock browser, so an own
    property on the instance fires on patched browsers and nothing else.
    """
    assert "Object.getPrototypeOf(obj)" in script
    assert "Object.defineProperty(obj" not in script


def test_nothing_touches_navigator_webdriver() -> None:
    """It is already false without --enable-automation; assigning it only dirties navigator."""
    assert "webdriver" not in _page_init_script(fingerprint_for("acct-1", "DE"))
    assert "webdriver" not in worker_init_script(fingerprint_for("acct-1", "DE"))


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


# ------------------------------------------------------------------ installed browser


def _fake_install(root: Any, exe_name: str, entries: tuple[str, ...]) -> Any:
    """A Chrome/Edge installation: the exe plus the version-named directory beside it."""
    application = root / "Application"
    application.mkdir(parents=True)
    for entry in entries:
        (application / entry).mkdir()
    exe = application / exe_name
    exe.write_text("", encoding="utf-8")
    return exe


def test_the_build_is_read_off_the_installation_before_any_browser_starts(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Any
) -> None:
    """``/json/version`` only answers DURING a launch, and the identity is built before it.

    Learning the build only at launch left the FIRST window of every run claiming a
    hardcoded milestone the engine is not — and a later window then presenting a
    different one for an account whose session was already recorded with the first,
    which is server-side observable with no JS at all.
    """
    exe = _fake_install(tmp_path, "chrome.exe", ("149.0.1.2", "150.0.3.4", "Locales"))
    monkeypatch.setattr(fp_module, "_observed", {})
    monkeypatch.setattr("core.web_login.browser.find_browser", lambda: exe)

    fingerprint = fingerprint_for("acct-1", "DE")

    assert fingerprint.chrome_full == "150.0.3.4"
    assert "Chrome/150.0.0.0" in fingerprint.user_agent


def test_an_edge_installation_is_recognised_as_edge(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Any
) -> None:
    exe = _fake_install(tmp_path, "msedge.exe", ("152.0.4191.62",))
    monkeypatch.setattr(fp_module, "_observed", {})
    monkeypatch.setattr("core.web_login.browser.find_browser", lambda: exe)

    fingerprint = fingerprint_for("acct-1", "DE")

    assert fingerprint.device.is_edge
    assert fingerprint.edge_full == "152.0.4191.62"


def test_no_build_is_invented_when_no_browser_can_be_read(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Any
) -> None:
    """There is no fallback constant: a claimed milestone is the installed one or none.

    Both ways of not knowing refuse — nothing installed, and an installation whose
    layout carries no version directory.
    """
    monkeypatch.setattr(fp_module, "_observed", {})

    def _missing() -> Any:
        msg = "no browser"
        raise BrowserNotFoundError(msg)

    monkeypatch.setattr("core.web_login.browser.find_browser", _missing)
    with pytest.raises(BrowserNotFoundError):
        fingerprint_for("acct-1", "DE")

    odd = _fake_install(tmp_path, "chrome.exe", ("Locales",))
    monkeypatch.setattr("core.web_login.browser.find_browser", lambda: odd)
    with pytest.raises(BrowserNotFoundError):
        fingerprint_for("acct-1", "DE")


@pytest.mark.parametrize("is_edge", [False, True])
def test_only_rows_matching_the_installed_browser_are_ever_handed_out(
    monkeypatch: pytest.MonkeyPatch,
    is_edge: bool,  # noqa: FBT001 - parametrized flag
) -> None:
    """Claiming Edge while a Chrome process answers every Edge feature probe is free to catch."""
    monkeypatch.setattr(
        fp_module, "_observed", {"build": _SEEN_BUILD, "edge": "1" if is_edge else ""}
    )

    brands = {fingerprint_for(f"acct-{i}", "DE").device.is_edge for i in range(60)}

    assert brands == {is_edge}


# ----------------------------------------------- an identity that does not move


def test_a_row_is_seeded_against_the_catalogue_not_against_the_live_table(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Reordering or shortening ``DEVICES`` must not re-seat a single account.

    Seeding on the table itself is what broke live: filtering it by the installed brand
    changed ``len(DEVICES)`` and therefore ``seed % len``, so an account whose Telegram
    session was recorded from macOS came back on the next connection claiming Windows —
    the exact contradiction the whole module exists to avoid.
    """
    before = {f"acct-{i}": fingerprint_for(f"acct-{i}", "DE").device.name for i in range(40)}

    # Patch what the module actually READS. ``fingerprint`` imports ``BY_NAME`` and
    # ``CATALOGUE``, never ``DEVICES`` — a setattr for "DEVICES" here lands on nothing,
    # and with ``raising=False`` it lands silently, leaving both sides of the assertion
    # computed by identical code. This test was inert for exactly that reason.
    shuffled = {name: BY_NAME[name] for name in reversed(BY_NAME)}
    monkeypatch.setattr(fp_module, "BY_NAME", shuffled)
    reordered = {f"acct-{i}": fingerprint_for(f"acct-{i}", "DE").device.name for i in range(40)}

    assert reordered == before


def test_the_brand_filter_moves_only_the_accounts_it_has_to(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """An Edge row never ships on a Chrome host — and nobody else pays for that rule."""
    monkeypatch.setattr(fp_module, "_observed", {"build": _SEEN_BUILD, "edge": ""})

    for i in range(80):
        account = f"acct-{i}"
        seated = _device_for(account, is_edge=False)
        catalogue_row = BY_NAME[CATALOGUE[_seed_index(account, len(CATALOGUE))]]
        assert seated.is_edge is False
        if not catalogue_row.is_edge:
            assert seated.name == catalogue_row.name


def test_the_identity_an_account_already_has_is_the_one_it_keeps() -> None:
    """Pinned to the rows accounts were ALREADY handed out, before the brand filter.

    A device row is not a private detail: Telegram has recorded it as the
    ``device_model`` / ``system_version`` of a live session, and an account that
    connected from macOS coming back from Windows is visible to anyone reading that
    account's Active Sessions list. The right-hand column is what the pre-filter
    mapping produced; the round that filtered ``DEVICES`` before seeding moved every
    single one of these (``acct-0`` to win11-chrome-intel, ``acct-1`` to
    macos-chrome-apple, ``acct-3`` to win11-chrome-nvidia, ...).
    """
    kept = {
        "acct-0": "macos-chrome-apple",
        "acct-1": "macos-chrome-amd",
        "acct-2": "win11-chrome-intel",
        "acct-3": "win11-chrome-intel",
        "acct-4": "macos-chrome-apple",
    }
    assert {a: fingerprint_for(a, "DE").device.name for a in kept} == kept
    # The one account that MUST move on a Chrome host: its catalogue row is the Edge one.
    assert BY_NAME[CATALOGUE[_seed_index("acct-5", len(CATALOGUE))]].is_edge
    assert not fingerprint_for("acct-5", "DE").device.is_edge


def test_the_catalogue_names_rows_that_exist() -> None:
    """Every seeded name must resolve, or the account it seats silently moves.

    A dangling name does not fail loudly — ``_device_for`` falls through to the brand
    fallback and hands that account a DIFFERENT row, which is the re-seating this whole
    area exists to prevent. The converse is deliberately allowed: ``DEVICES`` may hold a
    row the catalogue does not name yet, because adding one moves nobody until a name is
    put in the ring.
    """
    assert set(CATALOGUE) <= set(BY_NAME)


# -------------------------------------------------------- worker-scope client hints


def test_the_worker_publishes_the_same_client_hints_the_page_is_given() -> None:
    """``Emulation`` has no worker target and ``Network`` never answers on one.

    So the worker script carries ``navigator.userAgentData`` itself — and it must be the
    SAME object the page publishes, because two scopes of one browser disagreeing about
    the brand list is a far louder signal than any real value either could have shown.
    """
    fingerprint = fingerprint_for("acct-1", "DE")

    data = _config(worker_init_script(fingerprint))["uaData"]
    page = cast("dict[str, Any]", ua_override_params(fingerprint)["userAgentMetadata"])

    assert data["brands"] == page["brands"]
    assert data["platform"] == page["platform"]
    assert data["mobile"] == page["mobile"]
    assert data["high"]["fullVersionList"] == page["fullVersionList"]
    assert data["high"]["platformVersion"] == page["platformVersion"]
    assert data["high"]["architecture"] == page["architecture"]


def test_the_worker_dresses_the_real_useragentdata_object() -> None:
    """The declined objection to doing this in script was the prototype. So keep it.

    The real object is redefined over — its prototype, its constructor and its internal
    slots stay Chrome's own — rather than a hand-rolled stand-in whose ``constructor``
    is ``Object``. ``getHighEntropyValues`` delegates to the real method (so an invalid
    hint still rejects, and a hint we do not publish keeps the browser's own answer)
    and still returns a promise; ``toJSON`` and the frozen brand list are kept too.
    """
    script = worker_init_script(fingerprint_for("acct-1", "DE"))

    assert "navigator.userAgentData" in script
    assert "Object.getPrototypeOf(uad)" in script
    assert "Reflect.apply(high, this, arguments).then(" in script
    assert "Object.freeze(U.brands.map(" in script
    assert "proto.toJSON = F({" in script
    # Named and disguised like the natives they replace, as every other override is.
    assert "'getHighEntropyValues')" in script
    assert "'toJSON')" in script


def test_ua_override_params_carry_the_client_hints_the_ua_string_cannot() -> None:
    """One builder for both scopes: ``--user-agent`` rewrites the string and nothing else."""
    fingerprint = fingerprint_for("acct-1", "DE")

    params = ua_override_params(fingerprint)

    assert params["userAgent"] == fingerprint.user_agent
    assert params["acceptLanguage"] == fingerprint.accept_language
    assert params["platform"] == fingerprint.device.nav_platform
    metadata = cast("dict[str, Any]", params["userAgentMetadata"])
    assert metadata["platform"] == fingerprint.device.ua_platform


# ------------------------------------------------------------------- screen geometry


@pytest.mark.parametrize("device", DEVICES, ids=lambda d: d.name)
def test_no_device_claims_a_screen_with_no_room_for_its_os_shell(device: Any) -> None:
    """``availHeight == height`` is impossible on macOS and unusual on Windows."""
    assert 0 < device.screen_chrome < device.screen_h
    assert device.avail_height == device.screen_h - device.screen_chrome
    # The macOS menu bar is at the TOP, so it moves availTop; a taskbar does not.
    assert device.avail_top == (device.screen_chrome if device.is_mac else 0)
    assert device.device_pixel_ratio >= 1.0


def test_the_page_script_claims_a_coherent_screen_and_scale_factor() -> None:
    fingerprint = _fingerprint_with("macos-chrome-apple")

    config = _config(_page_init_script(fingerprint))

    assert config["availH"] < config["screenH"]
    assert config["availH"] == fingerprint.device.avail_height
    assert config["availTop"] == fingerprint.device.avail_top > 0
    assert config["dpr"] == fingerprint.device.device_pixel_ratio == 2.0


# ------------------------------------------------------------------ the worker clock


def test_the_worker_clock_matches_the_timezone_the_page_claims() -> None:
    """``Emulation.setTimezoneOverride`` reaches page sessions only.

    Without this a browser-level worker answers with the operator's own Windows zone
    while the page one frame away claims the account's.
    """
    fingerprint = fingerprint_for("acct-1", "DE")

    script = worker_init_script(fingerprint)

    assert _config(script)["timezone"] == fingerprint.timezone == "Europe/Berlin"
    assert "getTimezoneOffset" in script
    assert "Intl.DateTimeFormat = " in script


# ----------------------------------------------------------------- native disguise


@pytest.mark.parametrize(
    "script",
    [
        _page_init_script(fingerprint_for("acct-1", "DE")),
        worker_init_script(fingerprint_for("acct-1", "DE")),
        QR_CAPTURE_HOOK,
    ],
    ids=["page", "worker", "qr-hook"],
)
def test_every_replaced_function_reports_native_code(script: str) -> None:
    """``d.get.toString()`` returning ``"() => val"`` is a one-expression detector.

    Fixing the property DESCRIPTORS left the function identities behind: the source a
    replacement reports, and its ``.name``.
    """
    assert "[native code]" in script
    assert "Function.prototype.toString = shim" in script


def test_replaced_accessors_and_methods_carry_the_name_the_real_ones_do() -> None:
    """A native getter is ``get hardwareConcurrency``; ``getParameter`` is not ``""``.

    ``proto.getParameter = function (p) {...}`` assigns to a member expression, so
    NamedEvaluation never runs and the spoof is flagged by ``.name`` alone.
    """
    page = _page_init_script(fingerprint_for("acct-1", "DE"))

    assert "'get ' + prop" in page
    assert "'getParameter')" in page
    for name in ("'addEventListener')", "'postMessage')", "'get onmessage'", "'set onmessage'"):
        assert name in QR_CAPTURE_HOOK
