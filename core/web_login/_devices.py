"""The curated desktop device rows an account's window can wear.

Split out of :mod:`core.web_login.fingerprint` byte-for-byte (file-size budget); the
resolution logic, the version discovery and the injected scripts all stay there.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class _Device:
    """One realistic, internally-consistent desktop browser identity.

    ``ua_template`` carries the browser version as ``{chrome}`` (and ``{edge}``)
    placeholders: the claimed version is not a property of the device row, it is the
    version of the chrome.exe actually running (see :func:`fingerprint.fingerprint_for`).
    """

    name: str
    ua_template: str
    ua_platform: str  # client-hints platform: "Windows" / "macOS"
    ua_platform_version: str  # client-hints platformVersion, e.g. "15.0.0"
    nav_platform: str  # navigator.platform: "Win32" / "MacIntel"
    hardware_concurrency: int
    # navigator.deviceMemory is quantized to {0.25, 0.5, 1, 2, 4, 8} and CAPPED at 8:
    # a machine with more RAM still reports 8, so anything above it is impossible.
    device_memory: int
    screen_w: int
    screen_h: int
    # CSS pixels the OS shell keeps for itself: the Windows taskbar (48 on 11, 40 on
    # 10, both divided by the scale factor) or the macOS menu bar. ``availHeight ==
    # height`` is impossible on macOS and unusual on Windows, so this is what makes
    # the screen numbers a set rather than six independent claims.
    screen_chrome: int
    device_pixel_ratio: float
    webgl_vendor: str
    webgl_renderer: str
    architecture: str = "x86"  # client-hints architecture: "x86" / "arm"
    is_edge: bool = False

    @property
    def is_mac(self) -> bool:
        return self.ua_platform == "macOS"

    @property
    def avail_height(self) -> int:
        return self.screen_h - self.screen_chrome

    @property
    def avail_top(self) -> int:
        """The macOS menu bar sits at the TOP; the Windows taskbar is at the bottom."""
        return self.screen_chrome if self.is_mac else 0


_WIN_UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/{chrome} Safari/537.36"
)
_MAC_UA = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like "
    "Gecko) Chrome/{chrome} Safari/537.36"
)
_EDGE_UA = f"{_WIN_UA} Edg/{{edge}}"

# A small curated set of current, plausible DESKTOP identities (Telegram Web /k/ is a
# desktop web app). Each row is self-consistent: a macOS row carries MacIntel + a Mac UA
# + an Apple GPU + a Retina scale factor; Windows rows carry Win32 + a Windows UA + an
# Intel/NVIDIA/AMD GPU. Only the rows matching the INSTALLED browser are ever handed
# out (see :func:`fingerprint_for`), so a window never claims Edge while running Chrome.
DEVICES: tuple[_Device, ...] = (
    _Device(
        name="win11-chrome-intel",
        ua_template=_WIN_UA,
        ua_platform="Windows",
        ua_platform_version="15.0.0",
        nav_platform="Win32",
        hardware_concurrency=8,
        device_memory=8,
        screen_w=1920,
        screen_h=1080,
        screen_chrome=48,
        device_pixel_ratio=1.0,
        webgl_vendor="Google Inc. (Intel)",
        webgl_renderer=(
            "ANGLE (Intel, Intel(R) UHD Graphics 630 (0x00003E9B) Direct3D11 vs_5_0 ps_5_0, D3D11)"
        ),
    ),
    _Device(
        name="win11-chrome-nvidia",
        ua_template=_WIN_UA,
        ua_platform="Windows",
        ua_platform_version="15.0.0",
        nav_platform="Win32",
        hardware_concurrency=16,
        device_memory=8,
        screen_w=2560,
        screen_h=1440,
        screen_chrome=48,
        device_pixel_ratio=1.0,
        webgl_vendor="Google Inc. (NVIDIA)",
        webgl_renderer=(
            "ANGLE (NVIDIA, NVIDIA GeForce RTX 3060 (0x00002504) Direct3D11 vs_5_0 ps_5_0, D3D11)"
        ),
    ),
    _Device(
        name="win10-chrome-amd",
        ua_template=_WIN_UA,
        ua_platform="Windows",
        ua_platform_version="10.0.0",
        nav_platform="Win32",
        hardware_concurrency=12,
        device_memory=8,
        screen_w=1920,
        screen_h=1080,
        # The Windows 10 taskbar is 40 CSS px, not 11's 48.
        screen_chrome=40,
        device_pixel_ratio=1.0,
        webgl_vendor="Google Inc. (AMD)",
        webgl_renderer=(
            "ANGLE (AMD, AMD Radeon RX 6600 (0x000073FF) Direct3D11 vs_5_0 ps_5_0, D3D11)"
        ),
    ),
    _Device(
        name="win11-edge-intel",
        ua_template=_EDGE_UA,
        ua_platform="Windows",
        ua_platform_version="15.0.0",
        nav_platform="Win32",
        hardware_concurrency=8,
        device_memory=8,
        # 1536x864 IS a 1920x1080 panel at 125%, which is why the scale factor and the
        # taskbar (48 physical px, 38 CSS px) both follow from the resolution.
        screen_w=1536,
        screen_h=864,
        screen_chrome=38,
        device_pixel_ratio=1.25,
        webgl_vendor="Google Inc. (Intel)",
        webgl_renderer=(
            "ANGLE (Intel, Intel(R) Iris(R) Xe Graphics (0x0000A7A0) Direct3D11 vs_5_0 "
            "ps_5_0, D3D11)"
        ),
        is_edge=True,
    ),
    _Device(
        name="macos-chrome-apple",
        ua_template=_MAC_UA,
        ua_platform="macOS",
        ua_platform_version="14.6.1",
        nav_platform="MacIntel",
        hardware_concurrency=10,
        device_memory=8,
        # An Apple Studio Display: 5120x2880 physical, 2560x1440 logical at 2x.
        screen_w=2560,
        screen_h=1440,
        screen_chrome=25,
        device_pixel_ratio=2.0,
        webgl_vendor="Google Inc. (Apple)",
        webgl_renderer="ANGLE (Apple, ANGLE Metal Renderer: Apple M2, Unspecified Version)",
        # An M-series Mac is arm64; Chrome still says "Intel Mac OS X" in the UA, but
        # the client-hints architecture is the one place it tells the truth.
        architecture="arm",
    ),
    _Device(
        name="macos-chrome-amd",
        ua_template=_MAC_UA,
        ua_platform="macOS",
        ua_platform_version="13.6.0",
        nav_platform="MacIntel",
        hardware_concurrency=8,
        device_memory=8,
        screen_w=1920,
        screen_h=1080,
        screen_chrome=25,
        device_pixel_ratio=1.0,
        webgl_vendor="Google Inc. (AMD)",
        webgl_renderer=("ANGLE (AMD, AMD Radeon Pro 5500M OpenGL Engine, OpenGL 4.1 Metal - 89.3)"),
    ),
)

BY_NAME: dict[str, _Device] = {device.name: device for device in DEVICES}

# The seeding ring. An account's row is chosen by hashing its id into THIS list, never
# into ``DEVICES``: a table that is reordered, extended, shortened or filtered by brand
# would otherwise move every account at once, and an account whose Telegram session was
# recorded from macOS coming back as Windows is precisely the contradiction the whole
# module exists to avoid. So the list is FIXED — appending a name here re-seats accounts
# and is a deliberate decision, while adding a row to ``DEVICES`` alone moves nobody
# (it is simply not handed out until a name is put here). Every name here MUST resolve
# to a row: ``fingerprint._device_for`` guards against a dangling one, but the guard
# re-seats that account rather than failing, so a test pins the names instead.
CATALOGUE: tuple[str, ...] = (
    "win11-chrome-intel",
    "win11-chrome-nvidia",
    "win10-chrome-amd",
    "win11-edge-intel",
    "macos-chrome-apple",
    "macos-chrome-amd",
)
