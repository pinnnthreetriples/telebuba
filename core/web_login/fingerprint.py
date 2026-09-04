"""Per-account browser fingerprint: each account's web session shows its OWN device.

Telegram records a web session from exactly three values WebK reads off ``navigator``
and sends in ``initConnection`` (tweb's ``networkerFactory``): ``device_model`` from
``userAgent``, ``system_version`` from ``platform``, and ``system_lang_code`` from
``language``. WebK reads them inside its MTProto **worker**, which a page-level
override never reaches — so the same identity is applied twice, once to the page
(:func:`apply_page_fingerprint`) and once inside every worker before its script runs
(:func:`worker_init_script`). The rest of the machine-identifying surface (timezone,
WebGL, hardware, screen) is page-only: it never reaches Telegram, and is overridden
so the page itself cannot fingerprint the operator's real machine.

The device is chosen deterministically from ``account_id`` (a given account always looks
like the same machine); timezone and language are aligned to the account's proxy country
when known, so the session looks native to its exit IP.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from core.web_login._cdp import CdpSession


@dataclass(frozen=True)
class _Device:
    """One realistic, internally-consistent desktop browser identity."""

    name: str
    user_agent: str
    ua_platform: str  # client-hints platform: "Windows" / "macOS"
    ua_platform_version: str  # client-hints platformVersion, e.g. "15.0.0"
    nav_platform: str  # navigator.platform: "Win32" / "MacIntel"
    chrome_major: str
    chrome_full: str
    hardware_concurrency: int
    device_memory: int
    screen_w: int
    screen_h: int
    webgl_vendor: str
    webgl_renderer: str
    is_edge: bool = False


_CHROME = "151.0.7922.76"
_CHROME_MAJ = "151"
_WIN_UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) "
    f"Chrome/{_CHROME} Safari/537.36"
)
_MAC_UA = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like "
    f"Gecko) Chrome/{_CHROME} Safari/537.36"
)

# A small curated set of current, plausible DESKTOP identities (Telegram Web /k/ is a
# desktop web app). Each row is self-consistent: a macOS row carries MacIntel + a Mac UA
# + an Apple GPU; Windows rows carry Win32 + a Windows UA + an Intel/NVIDIA/AMD GPU.
DEVICES: tuple[_Device, ...] = (
    _Device(
        name="win11-chrome-intel",
        user_agent=_WIN_UA,
        ua_platform="Windows",
        ua_platform_version="15.0.0",
        nav_platform="Win32",
        chrome_major=_CHROME_MAJ,
        chrome_full=_CHROME,
        hardware_concurrency=8,
        device_memory=8,
        screen_w=1920,
        screen_h=1080,
        webgl_vendor="Google Inc. (Intel)",
        webgl_renderer=(
            "ANGLE (Intel, Intel(R) UHD Graphics 630 (0x00003E9B) Direct3D11 vs_5_0 ps_5_0, D3D11)"
        ),
    ),
    _Device(
        name="win11-chrome-nvidia",
        user_agent=_WIN_UA,
        ua_platform="Windows",
        ua_platform_version="15.0.0",
        nav_platform="Win32",
        chrome_major=_CHROME_MAJ,
        chrome_full=_CHROME,
        hardware_concurrency=16,
        device_memory=16,
        screen_w=2560,
        screen_h=1440,
        webgl_vendor="Google Inc. (NVIDIA)",
        webgl_renderer=(
            "ANGLE (NVIDIA, NVIDIA GeForce RTX 3060 (0x00002504) Direct3D11 vs_5_0 ps_5_0, D3D11)"
        ),
    ),
    _Device(
        name="win10-chrome-amd",
        user_agent=_WIN_UA,
        ua_platform="Windows",
        ua_platform_version="10.0.0",
        nav_platform="Win32",
        chrome_major=_CHROME_MAJ,
        chrome_full=_CHROME,
        hardware_concurrency=12,
        device_memory=16,
        screen_w=1920,
        screen_h=1080,
        webgl_vendor="Google Inc. (AMD)",
        webgl_renderer=(
            "ANGLE (AMD, AMD Radeon RX 6600 (0x000073FF) Direct3D11 vs_5_0 ps_5_0, D3D11)"
        ),
    ),
    _Device(
        name="win11-edge-intel",
        user_agent=f"{_WIN_UA} Edg/{_CHROME}",
        ua_platform="Windows",
        ua_platform_version="15.0.0",
        nav_platform="Win32",
        chrome_major=_CHROME_MAJ,
        chrome_full=_CHROME,
        hardware_concurrency=8,
        device_memory=8,
        screen_w=1536,
        screen_h=864,
        webgl_vendor="Google Inc. (Intel)",
        webgl_renderer=(
            "ANGLE (Intel, Intel(R) Iris(R) Xe Graphics (0x0000A7A0) Direct3D11 vs_5_0 "
            "ps_5_0, D3D11)"
        ),
        is_edge=True,
    ),
    _Device(
        name="macos-chrome-apple",
        user_agent=_MAC_UA,
        ua_platform="macOS",
        ua_platform_version="14.6.1",
        nav_platform="MacIntel",
        chrome_major=_CHROME_MAJ,
        chrome_full=_CHROME,
        hardware_concurrency=10,
        device_memory=16,
        screen_w=2560,
        screen_h=1440,
        webgl_vendor="Google Inc. (Apple)",
        webgl_renderer="ANGLE (Apple, ANGLE Metal Renderer: Apple M2, Unspecified Version)",
    ),
    _Device(
        name="macos-chrome-amd",
        user_agent=_MAC_UA,
        ua_platform="macOS",
        ua_platform_version="13.6.0",
        nav_platform="MacIntel",
        chrome_major=_CHROME_MAJ,
        chrome_full=_CHROME,
        hardware_concurrency=8,
        device_memory=16,
        screen_w=1920,
        screen_h=1080,
        webgl_vendor="Google Inc. (AMD)",
        webgl_renderer=("ANGLE (AMD, AMD Radeon Pro 5500M OpenGL Engine, OpenGL 4.1 Metal - 89.3)"),
    ),
)

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
_DEFAULT_LOCALE = ("Etc/UTC", "en-US")


@dataclass(frozen=True)
class Fingerprint:
    """A resolved per-account identity: a device plus a timezone/locale for its proxy."""

    device: _Device
    timezone: str
    locale: str  # primary BCP-47, e.g. "de-DE"
    languages: tuple[str, ...]
    accept_language: str

    @property
    def user_agent(self) -> str:
        return self.device.user_agent


def _seed_index(account_id: str, modulo: int) -> int:
    digest = hashlib.sha256(account_id.encode("utf-8")).hexdigest()
    return int(digest, 16) % modulo


def fingerprint_for(account_id: str, country_code: str | None = None) -> Fingerprint:
    """Deterministic per-account identity; timezone/locale aligned to the proxy country."""
    device = DEVICES[_seed_index(account_id, len(DEVICES))]
    timezone, locale = _COUNTRY.get((country_code or "").upper(), _DEFAULT_LOCALE)
    primary = locale.split("-")[0]
    languages: tuple[str, ...] = (locale, primary) if primary != locale else (locale,)
    if primary != "en":
        languages = (*languages, "en")
    accept_language = f"{locale},{primary};q=0.9,en;q=0.8"
    return Fingerprint(
        device=device,
        timezone=timezone,
        locale=locale,
        languages=languages,
        accept_language=accept_language,
    )


def _ua_metadata(device: _Device) -> dict[str, object]:
    """Client-Hints ``userAgentMetadata`` consistent with the device's UA string."""
    not_a = {"brand": "Not)A;Brand", "version": "99"}
    not_a_full = {"brand": "Not)A;Brand", "version": "99.0.0.0"}
    chromium = {"brand": "Chromium", "version": device.chrome_major}
    chromium_full = {"brand": "Chromium", "version": device.chrome_full}
    label = "Microsoft Edge" if device.is_edge else "Google Chrome"
    named = {"brand": label, "version": device.chrome_major}
    named_full = {"brand": label, "version": device.chrome_full}
    return {
        "brands": [not_a, chromium, named],
        "fullVersionList": [not_a_full, chromium_full, named_full],
        "platform": device.ua_platform,
        "platformVersion": device.ua_platform_version,
        "architecture": "x86",
        "bitness": "64",
        "model": "",
        "mobile": False,
        "wow64": False,
    }


# Document-start script: hardens what the Emulation domain does NOT already cover.
# ``setUserAgentOverride`` alone settles the page's userAgent, platform and languages,
# so this only adds the surface it leaves real. Config arrives as an injected ``__FP__``
# literal, and every override is wrapped so a failure can never break page load. (No
# canvas patch: Telegram does not fingerprint canvas for the session record, and WebK
# draws its QR to a canvas.)
_INIT_TEMPLATE = r"""
(() => {
  const C = __FP__;
  const def = (obj, prop, val) => {
    try { Object.defineProperty(obj, prop, { get: () => val, configurable: true }); }
    catch (e) {}
  };
  def(navigator, 'hardwareConcurrency', C.hardwareConcurrency);
  def(navigator, 'deviceMemory', C.deviceMemory);
  def(navigator, 'webdriver', false);
  def(screen, 'width', C.screenW);
  def(screen, 'height', C.screenH);
  def(screen, 'availWidth', C.screenW);
  def(screen, 'availHeight', C.screenH);
  def(screen, 'colorDepth', 24);
  def(screen, 'pixelDepth', 24);
  const patchGL = (proto) => {
    if (!proto || !proto.getParameter) return;
    try {
      const orig = proto.getParameter;
      proto.getParameter = function (p) {
        if (p === 37445) return C.webglVendor;
        if (p === 37446) return C.webglRenderer;
        return orig.call(this, p);
      };
    } catch (e) {}
  };
  patchGL(self.WebGLRenderingContext && WebGLRenderingContext.prototype);
  patchGL(self.WebGL2RenderingContext && WebGL2RenderingContext.prototype);
})();
"""


# Worker-scope overrides. A worker has no ``screen`` and no ``Page`` domain, so this is
# just the ``navigator`` surface — and it carries ``userAgent`` too, because a SHARED
# worker is a browser-level target that does not inherit the page's UA override.
# Evaluated on the worker's own session while it is paused on start, so WebK's
# ``initConnection`` reads these and never the real machine's values.
_WORKER_TEMPLATE = r"""
(() => {
  const C = __FP__;
  const def = (obj, prop, val) => {
    try { Object.defineProperty(obj, prop, { get: () => val, configurable: true }); }
    catch (e) {}
  };
  def(navigator, 'userAgent', C.userAgent);
  def(navigator, 'platform', C.navPlatform);
  def(navigator, 'language', C.locale);
  def(navigator, 'languages', Object.freeze(C.languages.slice()));
  def(navigator, 'hardwareConcurrency', C.hardwareConcurrency);
  def(navigator, 'deviceMemory', C.deviceMemory);
})();
"""


def _fill(template: str, config: dict[str, object]) -> str:
    return template.replace("__FP__", json.dumps(config))


def _page_init_script(fp: Fingerprint) -> str:
    return _fill(
        _INIT_TEMPLATE,
        {
            "hardwareConcurrency": fp.device.hardware_concurrency,
            "deviceMemory": fp.device.device_memory,
            "screenW": fp.device.screen_w,
            "screenH": fp.device.screen_h,
            "webglVendor": fp.device.webgl_vendor,
            "webglRenderer": fp.device.webgl_renderer,
        },
    )


def worker_init_script(fp: Fingerprint) -> str:
    """The override to evaluate inside a worker before its own script runs."""
    return _fill(
        _WORKER_TEMPLATE,
        {
            "userAgent": fp.user_agent,
            "navPlatform": fp.device.nav_platform,
            "locale": fp.locale,
            "languages": list(fp.languages),
            "hardwareConcurrency": fp.device.hardware_concurrency,
            "deviceMemory": fp.device.device_memory,
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
        "Emulation.setUserAgentOverride",
        {
            "userAgent": fp.user_agent,
            "acceptLanguage": fp.accept_language,
            "platform": fp.device.nav_platform,
            "userAgentMetadata": _ua_metadata(fp.device),
        },
        session_id=session_id,
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
