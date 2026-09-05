"""Per-account browser fingerprint: each account's web session shows its OWN device.

Telegram records a web session from exactly three values WebK reads off ``navigator``
and sends in ``initConnection`` (tweb's ``networkerFactory``): ``device_model`` from
``userAgent``, ``system_version`` from ``platform``, and ``system_lang_code`` from
``language``. WebK reads them inside its MTProto **worker**, which a page-level
override never reaches — so the same identity is applied twice, once to the page
(:func:`apply_page_fingerprint`) and once inside every worker before its script runs
(:func:`worker_init_script`, which carries the client hints too). The
rest of the machine-identifying surface never reaches Telegram, and is overridden so the
page itself cannot fingerprint the operator's real machine: screen is page-only (a worker
has none), while timezone, hardware and WebGL are applied in BOTH scopes, since anything
a worker answers differently from its own page is a contradiction inside one window.

The device is chosen deterministically from ``account_id`` against a FIXED catalogue of
row names (a given account always looks like the same machine, whatever the device table
does), and re-seated only when that row's brand is not the binary this host will actually
launch; timezone and language are aligned to the account's proxy country.

An unresolvable country (:data:`_DEFAULT_LOCALE`) claims no place at all. Naming a
concrete zone would be a positive geographic claim, and comparing the claimed timezone
against the exit IP's geolocation is the single most routinely computed geo check there
is — so a wrong zone is a contradiction anyone can compute, whereas a plain UTC offset
is merely uncommon on a desktop. The caller resolves the country first (see
``services.accounts.web_login``), so this only applies when the proxy check itself
could not answer — i.e. when the browser is not reaching Telegram anyway.
"""

from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass
from typing import TYPE_CHECKING

from core.web_login._devices import BY_NAME, CATALOGUE, _Device
from core.web_login._scripts import PAGE_TEMPLATE, WORKER_TEMPLATE, fill

if TYPE_CHECKING:
    from pathlib import Path

    from core.web_login._cdp import CdpSession


# country_code -> (IANA timezone, primary BCP-47 language). Small on purpose; the
# fallback keeps an unknown or absent country deterministic and consistent.
_COUNTRY: dict[str, tuple[str, str]] = {
    "US": ("America/New_York", "en-US"),
    "GB": ("Europe/London", "en-GB"),
    "NL": ("Europe/Amsterdam", "nl-NL"),
    "DE": ("Europe/Berlin", "de-DE"),
    "FR": ("Europe/Paris", "fr-FR"),
    "ES": ("Europe/Madrid", "es-ES"),
    "IT": ("Europe/Rome", "it-IT"),
    "PL": ("Europe/Warsaw", "pl-PL"),
    "FI": ("Europe/Helsinki", "fi-FI"),
    "SE": ("Europe/Stockholm", "sv-SE"),
    "TR": ("Europe/Istanbul", "tr-TR"),
    "RU": ("Europe/Moscow", "ru-RU"),
    "UA": ("Europe/Kyiv", "uk-UA"),
    "CA": ("America/Toronto", "en-CA"),
    "BR": ("America/Sao_Paulo", "pt-BR"),
    "IN": ("Asia/Kolkata", "en-IN"),
    "SG": ("Asia/Singapore", "en-SG"),
    "JP": ("Asia/Tokyo", "ja-JP"),
    "AE": ("Asia/Dubai", "ar-AE"),
}
# Unmapped country, or one no proxy check could resolve. See the module docstring: a
# zero offset claims no place, so nothing here can contradict the exit IP.
_DEFAULT_LOCALE = ("Etc/UTC", "en-US")

# The browser this host will launch, learned OFFLINE from the installed binary before
# the first window opens (``_discover_installed``) and refined from DevTools
# ``/json/version`` at launch. There is no hardcoded fallback build on purpose: it
# would be a claim about the engine that the engine itself contradicts on the very
# first window of every backend run, and JS feature detection is not overridden.
_observed: dict[str, str] = {}
# ``...\Google\Chrome\Application\148.0.7778.217\`` — both Chrome and Edge keep a
# version-named directory beside the executable, which is the installed build.
_BUILD_DIR = re.compile(r"^\d+(?:\.\d+){2,3}$")


def note_installed_browser(build: str, *, is_edge: bool) -> None:
    """Record the build and the brand of the browser this host actually launches."""
    _observed["build"] = build
    _observed["edge"] = "1" if is_edge else ""


def note_browser_version(browser_field: object) -> None:
    """Refine the build from ``/json/version``'s ``"Browser"`` ("Chrome/148.0.7222.0")."""
    if not isinstance(browser_field, str):
        return
    _, _, version = browser_field.rpartition("/")
    if "." in version and version.replace(".", "").isdigit():
        _observed["build"] = version


def _build_beside(exe: Path) -> str | None:
    """The newest version-named directory next to ``exe``, or ``None`` on an odd layout."""
    try:
        names = [item.name for item in exe.parent.iterdir() if _BUILD_DIR.match(item.name)]
    except OSError:
        return None
    if not names:
        return None
    return max(names, key=lambda name: tuple(int(part) for part in name.split(".")))


def _discover_installed() -> None:
    """Read the installed build and brand off disk, before any browser is launched.

    ``fingerprint_for`` runs before ``launch_account_web``, so waiting for DevTools
    ``/json/version`` would leave the first window of every run claiming a version
    nothing on this machine has — and a later window in the same run would then present
    a DIFFERENT one for a session Telegram already recorded, which browsers do not do.
    """
    from core.web_login.browser import (  # noqa: PLC0415 - avoids an import cycle
        BrowserNotFoundError,
        find_browser,
    )

    exe = find_browser()
    build = _build_beside(exe)
    if build is None:
        msg = "Could not read the installed browser's version; cannot open a web session."
        raise BrowserNotFoundError(msg)
    note_installed_browser(build, is_edge=exe.name.lower().startswith("msedge"))


def _installed() -> tuple[str, bool]:
    """``(build, is_edge)`` of the browser this host launches; discovered on first use."""
    if "build" not in _observed:
        _discover_installed()
    return _observed["build"], bool(_observed.get("edge"))


@dataclass(frozen=True)
class Fingerprint:
    """A resolved per-account identity: a device plus a timezone/locale for its proxy."""

    device: _Device
    timezone: str
    locale: str  # primary BCP-47, e.g. "de-DE"
    languages: tuple[str, ...]
    accept_language: str
    chrome_full: str

    @property
    def chrome_major(self) -> str:
        return self.chrome_full.split(".")[0]

    @property
    def edge_full(self) -> str:
        """Edge's own build (empty on a Chrome row).

        An Edge row is only ever handed out on a host whose installed binary IS Edge,
        so the discovered build already is Edge's own version rather than Chromium's.
        The Chromium entry in ``fullVersionList`` then repeats it: Edge's Chromium
        build cannot be derived from Edge's, and repeating the real one keeps the
        token every site actually reads (``Microsoft Edge``) truthful.
        """
        return self.chrome_full if self.device.is_edge else ""

    @property
    def ua_version(self) -> str:
        """The version the UA STRING may carry: ``<major>.0.0.0``, as real Chrome reports.

        UA reduction froze minor/build/patch at zero in ``navigator.userAgent`` (and in
        Edge's ``Edg/`` token, reduced the same way), so a full build there is an anomaly
        on the most-read string in the whole fingerprint. The true build stays where real
        browsers still publish it: the Client-Hints ``fullVersionList`` (:func:`_ua_metadata`).
        """
        return f"{self.chrome_major}.0.0.0"

    @property
    def user_agent(self) -> str:
        return self.device.ua_template.format(chrome=self.ua_version, edge=self.ua_version)


def _seed_index(account_id: str, modulo: int) -> int:
    digest = hashlib.sha256(account_id.encode("utf-8")).hexdigest()
    return int(digest, 16) % modulo


def _device_for(account_id: str, *, is_edge: bool) -> _Device:
    """The account's row: seeded against :data:`CATALOGUE`, never against the table.

    Seeding against the live table is what broke the module's central promise. Filtering
    ``DEVICES`` by the installed brand changed its length, and ``seed % len`` moved EVERY
    account's row — an account whose Telegram session was recorded from macOS came back
    the next connection claiming Windows, which is the exact contradiction this module
    exists to avoid. :data:`CATALOGUE` is fixed, so rows may be added, removed, reordered
    or filtered without moving anybody.

    The brand fallback re-seats ONLY an account whose catalogue row is the wrong brand
    (an Edge row on a Chrome host, which every feature probe would contradict). It reads
    the catalogue too, so it is stable in the same way.
    """
    chosen = BY_NAME.get(CATALOGUE[_seed_index(account_id, len(CATALOGUE))])
    if chosen is not None and chosen.is_edge is is_edge:
        return chosen
    names = tuple(
        name
        for name in CATALOGUE
        if (row := BY_NAME.get(name)) is not None and row.is_edge is is_edge
    )
    return BY_NAME[names[_seed_index(f"{account_id}\x00brand", len(names))]]


def fingerprint_for(account_id: str, country_code: str | None = None) -> Fingerprint:
    """Deterministic per-account identity; timezone/locale aligned to the proxy country."""
    build, is_edge = _installed()
    device = _device_for(account_id, is_edge=is_edge)
    timezone, locale = _COUNTRY.get((country_code or "").upper(), _DEFAULT_LOCALE)
    primary = locale.split("-")[0]
    languages: tuple[str, ...] = (locale, primary) if primary != locale else (locale,)
    if primary != "en":
        languages = (*languages, "en")
    return Fingerprint(
        device=device,
        timezone=timezone,
        locale=locale,
        languages=languages,
        # Bare tags only: Chromium splits acceptLanguage on commas WITHOUT stripping
        # q-values, so a ";q=0.9" here would land in navigator.languages, where no real
        # browser ever puts one. Chrome adds the q-values to the outgoing header itself.
        accept_language=",".join(languages),
        chrome_full=build,
    )


def _ua_metadata(fp: Fingerprint) -> dict[str, object]:
    """Client-Hints ``userAgentMetadata`` consistent with the device's UA string."""
    device = fp.device
    full = fp.edge_full if device.is_edge else fp.chrome_full
    not_a = {"brand": "Not)A;Brand", "version": "99"}
    not_a_full = {"brand": "Not)A;Brand", "version": "99.0.0.0"}
    chromium = {"brand": "Chromium", "version": fp.chrome_major}
    chromium_full = {"brand": "Chromium", "version": fp.chrome_full}
    label = "Microsoft Edge" if device.is_edge else "Google Chrome"
    named = {"brand": label, "version": fp.chrome_major}
    named_full = {"brand": label, "version": full}
    return {
        "brands": [not_a, chromium, named],
        "fullVersionList": [not_a_full, chromium_full, named_full],
        "platform": device.ua_platform,
        "platformVersion": device.ua_platform_version,
        "architecture": device.architecture,
        "bitness": "64",
        "model": "",
        "mobile": False,
        "wow64": False,
    }


def _ua_data(fp: Fingerprint) -> dict[str, object]:
    """``navigator.userAgentData`` for the worker script, from the page's own metadata.

    Built from :func:`_ua_metadata` so the worker cannot publish a hint the page
    contradicts — a property-versus-property disagreement inside one browser is a far
    louder signal than any real value. Only the hints the page metadata actually sets
    are listed: anything else stays whatever the real browser answers, which is what
    the page falls back to as well.
    """
    meta = _ua_metadata(fp)
    return {
        "brands": meta["brands"],
        "mobile": meta["mobile"],
        "platform": meta["platform"],
        "high": {
            "architecture": meta["architecture"],
            "bitness": meta["bitness"],
            "fullVersionList": meta["fullVersionList"],
            "model": meta["model"],
            "platformVersion": meta["platformVersion"],
            "wow64": meta["wow64"],
        },
    }


def ua_override_params(fp: Fingerprint) -> dict[str, object]:
    """The four ``Emulation.setUserAgentOverride`` parameters for a page session.

    ``--user-agent`` rewrites the UA STRING only; ``userAgentMetadata`` is what keeps
    the page's ``navigator.userAgentData`` and its ``Sec-CH-UA`` request headers from
    contradicting the ``navigator.userAgent`` and ``navigator.platform`` beside them.
    A worker target has no ``Emulation`` domain and cannot be sent this over
    ``Network`` either (see :data:`core.web_login._scripts.WORKER_TEMPLATE`), so the
    worker half of the same identity is installed by :func:`worker_init_script`.
    """
    return {
        "userAgent": fp.user_agent,
        "acceptLanguage": fp.accept_language,
        "platform": fp.device.nav_platform,
        "userAgentMetadata": _ua_metadata(fp),
    }


def _page_init_script(fp: Fingerprint) -> str:
    device = fp.device
    return fill(
        PAGE_TEMPLATE,
        {
            "hardwareConcurrency": device.hardware_concurrency,
            "deviceMemory": device.device_memory,
            "screenW": device.screen_w,
            "screenH": device.screen_h,
            "availW": device.screen_w,
            "availH": device.avail_height,
            "availTop": device.avail_top,
            "dpr": device.device_pixel_ratio,
            "webglVendor": device.webgl_vendor,
            "webglRenderer": device.webgl_renderer,
        },
    )


def worker_init_script(fp: Fingerprint) -> str:
    """The override to evaluate inside a worker before its own script runs."""
    return fill(
        WORKER_TEMPLATE,
        {
            "userAgent": fp.user_agent,
            "navPlatform": fp.device.nav_platform,
            "locale": fp.locale,
            "languages": list(fp.languages),
            "hardwareConcurrency": fp.device.hardware_concurrency,
            "deviceMemory": fp.device.device_memory,
            "timezone": fp.timezone,
            # The page's own strings: WebGL reaches a worker through OffscreenCanvas, so
            # a page-only patch made the two scopes answer getParameter(37446)
            # differently (claimed renderer in the page, null in either worker kind).
            "webglVendor": fp.device.webgl_vendor,
            "webglRenderer": fp.device.webgl_renderer,
            "uaData": _ua_data(fp),
        },
    )


async def apply_page_fingerprint(
    session: CdpSession,
    fp: Fingerprint,
    *,
    session_id: str,
) -> None:
    """Apply ``fp`` to one page target. MUST run before that page's first navigation.

    Sets the User-Agent + client hints, the timezone and locale, and installs a
    document-start script hardening the rest of the page surface. ``Page.enable``
    first: without the domain enabled the document-start script is never installed.
    """
    await session.send_command(
        "Emulation.setUserAgentOverride", ua_override_params(fp), session_id=session_id
    )
    await session.send_command(
        "Emulation.setTimezoneOverride", {"timezoneId": fp.timezone}, session_id=session_id
    )
    await session.send_command(
        "Emulation.setLocaleOverride", {"locale": fp.locale}, session_id=session_id
    )
    await session.send_command("Page.enable", session_id=session_id)
    await session.send_command(
        "Page.addScriptToEvaluateOnNewDocument",
        {"source": _page_init_script(fp)},
        session_id=session_id,
    )
