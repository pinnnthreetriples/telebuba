"""microG: a phone's Google apps, as the privileged system apps a Pixel has on /product.

redroid's images have no Google apps. A phone has Play services, its Services
Framework and the Play Store in ``/product/priv-app`` (``PrebuiltGmsCore``,
``GoogleServicesFramework``, ``Phonesky``); microG's own builds of the three (GmsCore,
GsfProxy and its Companion, FakeStore), all signed with microG's key, take those
places and names, so ``com.google.android.gms``, ``com.google.android.gsf`` and
``com.android.vending`` are system apps there. Their privileged permissions need no
allowlist: the images set ``ro.control_privapp_permissions=disable``.

The APKs are the ones microG publishes (https://microg.org/download.html: its GitHub
releases), pinned here by version and sha256 and fetched by
``core.emulator.assets.ensure_microg``; a new version is a new pin. PackageManager
does not extract a system app's native libraries, so GmsCore's go beside it, in
``lib/<instruction set>/`` for the host's ABI (the image's primary one) only.

What they cannot be here: the images do not spoof signatures (no
``android.permission.FAKE_PACKAGE_SIGNATURE``), so an app's Play services library,
which checks that ``com.google.android.gms`` is signed by Google, finds it is not and
treats Play services as unavailable.
"""

from __future__ import annotations

import io
import zipfile
from typing import TYPE_CHECKING, NamedTuple

if TYPE_CHECKING:
    from collections.abc import Mapping
    from pathlib import Path


class MicrogApp(NamedTuple):
    name: str  # its directory and APK under /product/priv-app, a Pixel's name for it
    url: str
    sha256: str

    @property
    def file_name(self) -> str:
        """Its file on the host: the release's own name, so a new pin is a new file."""
        return self.url.rsplit("/", 1)[1]


_GMSCORE = "https://github.com/microg/GmsCore/releases/download/v0.3.17.252432/"
APPS = (
    MicrogApp(
        "PrebuiltGmsCore",
        f"{_GMSCORE}com.google.android.gms-252432034.apk",
        "e5e6cba078bdec812b5450599350e5c1a12c8b7438da5bf77123af170f8c1a35",
    ),
    MicrogApp(
        "Phonesky",
        f"{_GMSCORE}com.android.vending-84022634.apk",
        "9b8616011cc8f026bcbbe094df850794abb9d6d9f23214d7a8f8c4d1033ea012",
    ),
    MicrogApp(
        "GoogleServicesFramework",
        "https://github.com/microg/GsfProxy/releases/download/v0.1.0/GsfProxy.apk",
        "86891b174301f06a1c84187b545a0a2a57044c6b768f3e84e865908743349692",
    ),
)
# A container has microG once it has this file, the smallest of them.
PROBE = f"/product/priv-app/{APPS[-1].name}/{APPS[-1].name}.apk"
# An APK's ABI -> the instruction-set directory (libraries, dex2oat output; AOSP's names).
INSTRUCTION_SETS = {"x86_64": "x86_64", "arm64-v8a": "arm64"}


def product_files(apks: Mapping[str, Path], abi: str) -> dict[str, bytes]:
    """What goes into /product: each app's APK (by ``APPS`` name) and its ``abi`` libraries."""
    files: dict[str, bytes] = {}
    for app in APPS:
        folder = f"priv-app/{app.name}"
        apk = apks[app.name].read_bytes()
        files[f"{folder}/{app.name}.apk"] = apk
        files.update(_libraries(apk, abi, f"{folder}/lib/{INSTRUCTION_SETS[abi]}"))
    return files


def _libraries(apk: bytes, abi: str, folder: str) -> dict[str, bytes]:
    prefix = f"lib/{abi}/"
    with zipfile.ZipFile(io.BytesIO(apk)) as archive:
        return {
            f"{folder}/{name.removeprefix(prefix)}": archive.read(name)
            for name in archive.namelist()
            if name.startswith(prefix) and name.endswith(".so") and "/" not in name[len(prefix) :]
        }
