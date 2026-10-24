"""microG goes into a phone's /product as a Pixel's Google apps are: privileged system apps."""

from __future__ import annotations

import hashlib
import io
import zipfile
from typing import TYPE_CHECKING

import pytest
import respx

from core.config import settings
from core.emulator import EmulatorError, _microg, _sensors, assets, redroid
from tests.core.emulator.test_redroid import _SPEC
from tests.core.emulator.test_redroid_files import _IMAGE_PROP, _docker

if TYPE_CHECKING:
    import tarfile
    from pathlib import Path

    from tests.core.emulator.conftest import Console


def _apk(app: _microg.MicrogApp) -> bytes:
    """A stand-in for ``app``'s APK: GmsCore's carries a library for each of two ABIs."""
    out = io.BytesIO()
    with zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED) as apk:
        apk.writestr("AndroidManifest.xml", app.name)
        if app.name == "PrebuiltGmsCore":
            apk.writestr("lib/x86_64/libcronet.so", b"x86_64 code")
            apk.writestr("lib/arm64-v8a/libcronet.so", b"arm64 code")
    return out.getvalue()


# A docker whose containers hold an image's build.prop, on a 16-core VM.
_PHONE = _docker(_IMAGE_PROP)


@pytest.fixture(autouse=True)
def _phone(console: Console) -> None:
    console.answer = _PHONE


@pytest.fixture
def microg(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """The three APKs of microG already downloaded, on an x86_64 host."""
    directory = tmp_path / "microg"
    directory.mkdir()
    for app in _microg.APPS:
        (directory / app.file_name).write_bytes(_apk(app))
    monkeypatch.setattr(settings.emulator, "microg_dir", directory)
    monkeypatch.setattr(_sensors, "_host_abi", "x86_64")
    monkeypatch.setattr(assets, "_LOCKS", {})
    return directory


def _given(console: Console, name: str) -> dict[str, tuple[tarfile.TarInfo, bytes]]:
    """What the container was given under /product/priv-app; empty when nothing."""
    entries: dict[str, tuple[tarfile.TarInfo, bytes]] = {}
    for copy in console.copies_into(f"{name}:/product"):
        archive = console.archive(copy)
        entries.update({n: e for n, e in archive.items() if n.startswith("priv-app")})
    return entries


@pytest.mark.asyncio
async def test_a_new_container_gets_microg_as_privileged_system_apps(
    console: Console, microg: Path
) -> None:
    await redroid.create_instance("telebuba-new", _SPEC, index=1, gateway="telebuba-new-net")

    given = _given(console, "telebuba-new")
    for app in _microg.APPS:
        info, data = given[f"priv-app/{app.name}/{app.name}.apk"]
        assert data == (microg / app.file_name).read_bytes(), "microG's own file, unchanged"
        assert (info.mode, info.uid, info.gid) == (0o644, 0, 0)
    libraries = [name for name, (info, _data) in given.items() if "/lib/" in name and info.isfile()]
    assert libraries == ["priv-app/PrebuiltGmsCore/lib/x86_64/libcronet.so"], "the host's ABI"
    assert given[libraries[0]][1] == b"x86_64 code"
    for name, (info, _data) in given.items():
        expected = (0o755 if info.isdir() else 0o644, 0, 0)
        assert (info.mode, info.uid, info.gid) == expected, name


@pytest.mark.asyncio
@pytest.mark.parametrize("has_it", [True, False])
@pytest.mark.usefixtures("microg")
async def test_a_refresh_gives_microg_only_to_a_container_without_it(
    console: Console, *, has_it: bool
) -> None:
    def answer(argv: list[str]) -> str:
        if argv[1] == "cp" and argv[2].endswith(_microg.PROBE) and not has_it:
            code = "emulator_command_failed"
            raise EmulatorError(code)
        return _PHONE(argv)

    console.answer = answer

    await redroid.refresh_identity("telebuba-acc-2", _SPEC, 2)

    assert bool(_given(console, "telebuba-acc-2")) is not has_it


@pytest.mark.asyncio
async def test_with_microg_off_a_container_gets_none(console: Console) -> None:
    assert settings.emulator.microg_dir is None  # the tests' default

    await redroid.create_instance("telebuba-new", _SPEC, index=1, gateway="telebuba-new-net")

    assert _given(console, "telebuba-new") == {}


@pytest.mark.asyncio
async def test_a_failed_download_leaves_the_phone_without_microg(
    console: Console, microg: Path
) -> None:
    for apk in microg.iterdir():
        apk.unlink()

    with respx.mock(assert_all_called=False) as github:
        github.get(url__startswith="https://github.com/microg/").respond(503)
        await redroid.create_instance("telebuba-new", _SPEC, index=1, gateway="telebuba-new-net")

    assert _given(console, "telebuba-new") == {}, "the phone is made all the same"
    assert list(microg.iterdir()) == [], "nothing half-downloaded is kept"


@pytest.mark.asyncio
async def test_microgs_apks_are_fetched_from_its_releases_and_kept_on_their_hash(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    bodies = {app.name: _apk(app) for app in _microg.APPS}
    pinned = tuple(
        app._replace(sha256=hashlib.sha256(bodies[app.name]).hexdigest()) for app in _microg.APPS
    )
    monkeypatch.setattr(_microg, "APPS", pinned)
    monkeypatch.setattr(settings.emulator, "microg_dir", tmp_path / "microg")
    monkeypatch.setattr(assets, "_LOCKS", {})

    with respx.mock(assert_all_called=True) as github:
        for app in pinned:
            github.get(app.url).respond(200, content=bodies[app.name])
        apks = await assets.ensure_microg()

    assert {name: path.read_bytes() for name, path in apks.items()} == bodies
    assert all(path.parent == tmp_path / "microg" for path in apks.values())


@pytest.mark.asyncio
async def test_a_microg_apk_of_another_hash_is_refused(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(settings.emulator, "microg_dir", tmp_path / "microg")
    monkeypatch.setattr(assets, "_LOCKS", {})

    with respx.mock(assert_all_called=False) as github:
        github.get(_microg.APPS[0].url).respond(200, content=b"not microG's")
        with pytest.raises(EmulatorError) as refused:
            await assets.ensure_microg()

    assert refused.value.code == "emulator_checksum_mismatch"
    assert list((tmp_path / "microg").iterdir()) == []


def test_the_pins_are_microgs_official_releases() -> None:
    for app in _microg.APPS:
        assert app.url.startswith("https://github.com/microg/"), app.url
        assert len(app.sha256) == 64
    assert [app.name for app in _microg.APPS] == [
        "PrebuiltGmsCore",
        "Phonesky",
        "GoogleServicesFramework",
    ]
