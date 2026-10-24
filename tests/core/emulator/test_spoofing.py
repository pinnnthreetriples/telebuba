"""The signature-spoofing patch goes into a container's /system/framework before boot."""

from __future__ import annotations

from typing import TYPE_CHECKING

import pytest

from core.config import settings
from core.emulator import EmulatorError, _provision, _sensors, redroid
from tests.core.emulator.test_redroid import _SPEC
from tests.core.emulator.test_redroid_files import _IMAGE_PROP, _docker

if TYPE_CHECKING:
    import tarfile
    from pathlib import Path

    from tests.core.emulator.conftest import Console

# A docker whose containers hold an image's build.prop, on a 16-core VM.
_PHONE = _docker(_IMAGE_PROP)
_PATCHED = b"patched services.jar bytes"


@pytest.fixture(autouse=True)
def _phone(console: Console) -> None:
    console.answer = _PHONE


@pytest.fixture
def patch(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> bytes:
    """A built patch for _SPEC's SDK (33), on an x86_64 host."""
    directory = tmp_path / "framework-patch"
    (directory / str(_SPEC.sdk)).mkdir(parents=True)
    (directory / str(_SPEC.sdk) / "services.jar").write_bytes(_PATCHED)
    monkeypatch.setattr(settings.emulator, "framework_patch_dir", directory)
    monkeypatch.setattr(_sensors, "_host_abi", "x86_64")
    return _PATCHED


def _framework(console: Console, name: str) -> dict[str, tuple[tarfile.TarInfo, bytes]]:
    """What the container was given under /system/framework; empty when nothing."""
    entries: dict[str, tuple[tarfile.TarInfo, bytes]] = {}
    for copy in console.copies_into(f"{name}:/system/framework"):
        entries.update(console.archive(copy))
    return entries


@pytest.mark.asyncio
async def test_a_new_container_gets_the_patch_and_a_deodexed_services(
    console: Console, patch: bytes
) -> None:
    await redroid.create_instance("telebuba-new", _SPEC, index=1, gateway="telebuba-new-net")

    given = _framework(console, "telebuba-new")
    jar, data = given["services.jar"]
    assert data == patch, "the built, patched services.jar"
    assert (jar.mode, jar.uid, jar.gid) == (0o644, 0, 0), "as the image's own"
    for stale in ("services.odex", "services.vdex", "services.art"):
        info, body = given[f"oat/x86_64/{stale}"]
        assert body == b"\x00", f"{stale} invalidated so ART runs the new dex"
    assert _provision.SPOOF_MARKER.rsplit("/", 1)[1] in given, "a marker records the patch"
    for name, (info, _data) in given.items():
        expected = (0o755 if info.isdir() else 0o644, 0, 0)
        assert (info.mode, info.uid, info.gid) == expected, name


@pytest.mark.asyncio
@pytest.mark.parametrize("has_it", [True, False])
@pytest.mark.usefixtures("patch")
async def test_a_refresh_patches_only_a_container_without_it(
    console: Console, *, has_it: bool
) -> None:
    marker = _provision.SPOOF_MARKER

    def answer(argv: list[str]) -> str:
        if argv[1] == "cp" and argv[2].endswith(marker) and not has_it:
            code = "emulator_command_failed"
            raise EmulatorError(code)
        return _PHONE(argv)

    console.answer = answer

    await redroid.refresh_identity("telebuba-acc-2", _SPEC, 2)

    assert bool(_framework(console, "telebuba-acc-2")) is not has_it


@pytest.mark.asyncio
async def test_with_spoofing_off_a_container_gets_nothing(console: Console) -> None:
    assert settings.emulator.framework_patch_dir is None  # the tests' default

    await redroid.create_instance("telebuba-new", _SPEC, index=1, gateway="telebuba-new-net")

    assert _framework(console, "telebuba-new") == {}


@pytest.mark.asyncio
@pytest.mark.usefixtures("patch")
async def test_without_a_patch_for_the_sdk_a_container_gets_nothing(
    console: Console, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(settings.emulator, "framework_patch_dir", tmp_path / "empty")

    await redroid.create_instance("telebuba-new", _SPEC, index=1, gateway="telebuba-new-net")

    assert _framework(console, "telebuba-new") == {}, "the phone boots unpatched, no crash"
