"""Shared /data: an SDK's read-only base under each pod's own diff, opt-in and kept apart."""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING

import pytest
from pydantic import ValidationError

from core._config_emulator import EmulatorSettings
from core.config import settings
from core.emulator import _data, host, pod
from core.emulator._errors import EmulatorError
from tests.core.emulator.test_pod import _PROXY, _SPEC, _call, _docker

if TYPE_CHECKING:
    from collections.abc import Callable

    from tests.core.emulator.conftest import Console

_SPEC_IMAGE = settings.emulator.redroid_images[_SPEC.sdk]


@pytest.fixture
def layers(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> tuple[Path, Path]:
    """Shared /data on, with a base prepared for ``_SPEC``'s SDK."""
    base, diff = tmp_path / "data-base", tmp_path / "data-diff"
    (base / str(_SPEC.sdk)).mkdir(parents=True)
    monkeypatch.setattr(settings.emulator, "data_base_dir", base)
    monkeypatch.setattr(settings.emulator, "data_diff_dir", diff)
    return base, diff


def _removals(console: Console) -> list[list[str]]:
    return [call[1:] for call in console.calls if call[1] == "run" and "rm" in call]


@pytest.mark.parametrize(
    "dirs",
    [
        {"data_base_dir": Path("/srv/data-base")},
        {"data_diff_dir": Path("/srv/data-diff")},
        {"data_base_dir": Path("data-base"), "data_diff_dir": Path("/srv/data-diff")},
    ],
)
def test_shared_data_takes_both_absolute_directories_or_none(dirs: dict[str, Path]) -> None:
    with pytest.raises(ValidationError):
        EmulatorSettings.model_validate(dirs)


def test_a_data_directory_never_climbs_out_with_dot_dot() -> None:
    with pytest.raises(ValidationError):
        EmulatorSettings(data_base_dir=Path("/srv/data-base"), data_diff_dir=Path("/var/.."))


def test_the_diff_directory_is_never_the_filesystem_root() -> None:
    with pytest.raises(ValidationError):
        EmulatorSettings(data_base_dir=Path("/srv/data-base"), data_diff_dir=Path("/"))


def test_empty_directories_in_the_env_file_are_off(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("EMULATOR__DATA_BASE_DIR", "")
    monkeypatch.setenv("EMULATOR__DATA_DIFF_DIR", "")

    off = EmulatorSettings()

    assert (off.data_base_dir, off.data_diff_dir) == (None, None)


def test_shared_data_is_off_unless_configured() -> None:
    configured = EmulatorSettings(data_base_dir=Path("/srv/b"), data_diff_dir=Path("/srv/d"))

    assert EmulatorSettings().data_base_dir is None
    assert configured.data_diff_dir is not None


@pytest.mark.asyncio
async def test_off_there_are_no_layers_and_nothing_to_remove(console: Console) -> None:
    assert await _data.data_layers("telebuba-acc", 34) is None
    await _data.remove_diff("telebuba-acc")

    assert console.calls == []


@pytest.mark.asyncio
async def test_a_pods_diff_is_made_under_its_sdks_base(layers: tuple[Path, Path]) -> None:
    base, diff = layers

    found = await _data.data_layers("telebuba-acc", _SPEC.sdk)

    assert found == (base / str(_SPEC.sdk), diff / "telebuba-acc", True)
    assert (diff / "telebuba-acc").is_dir()


@pytest.mark.asyncio
async def test_an_sdk_without_a_prepared_base_is_refused(layers: tuple[Path, Path]) -> None:
    _base, diff = layers

    with pytest.raises(EmulatorError) as refused:
        await _data.data_layers("telebuba-acc", 33)

    assert refused.value.code == "emulator_data_base_missing"
    assert not diff.exists(), "nothing is made for a pod that cannot boot"


@pytest.mark.parametrize("name", ["..", "a/b", "../telebuba-acc", ""])
@pytest.mark.usefixtures("layers")
@pytest.mark.asyncio
async def test_a_name_that_is_not_one_plain_directory_is_refused(
    console: Console, name: str
) -> None:
    with pytest.raises(EmulatorError):
        await _data.data_layers(name, _SPEC.sdk)
    with pytest.raises(EmulatorError):
        await _data.remove_diff(name)

    assert _removals(console) == []


@pytest.mark.asyncio
async def test_a_diff_is_removed_as_root_in_a_throwaway_container(
    console: Console, layers: tuple[Path, Path]
) -> None:
    _base, diff = layers

    await _data.remove_diff("telebuba-acc")

    assert _removals(console) == [
        [
            *("run", "--rm", "--volume", f"{diff.as_posix()}:/data-diff"),
            *("--entrypoint", "rm", "alpine:3.20", "-rf", "/data-diff/telebuba-acc"),
        ]
    ]


@pytest.mark.asyncio
async def test_a_new_pod_boots_on_its_layers(console: Console, layers: tuple[Path, Path]) -> None:
    base, diff = layers
    console.answer = _docker()

    await pod.start_pod("telebuba-new", _SPEC, _PROXY)

    android = _call(console, "create", "telebuba-new")
    volumes = [android[i + 1] for i, arg in enumerate(android) if arg == "--volume"]
    assert volumes == [
        f"{(base / str(_SPEC.sdk)).as_posix()}:/data-base:ro",
        f"{(diff / 'telebuba-new').as_posix()}:/data-diff",
    ]
    assert android[-1] == "androidboot.use_redroid_overlayfs=1"
    assert android.index("--volume") < android.index(_SPEC_IMAGE), "docker options"


@pytest.mark.asyncio
async def test_a_pod_without_shared_data_mounts_nothing(console: Console) -> None:
    console.answer = _docker()

    await pod.start_pod("telebuba-new", _SPEC, _PROXY)

    android = _call(console, "create", "telebuba-new")
    assert "--volume" not in android
    assert "androidboot.use_redroid_overlayfs=1" not in android


@pytest.mark.asyncio
async def test_a_missing_base_refuses_the_start_before_anything_is_created(
    console: Console, layers: tuple[Path, Path]
) -> None:
    base, _diff = layers
    (base / str(_SPEC.sdk)).rmdir()
    console.answer = _docker()

    with pytest.raises(EmulatorError) as refused:
        await pod.start_pod("telebuba-new", _SPEC, _PROXY)

    assert refused.value.code == "emulator_data_base_missing"
    assert [call for call in console.calls if call[1] in {"create", "start"}] == []


@pytest.mark.asyncio
async def test_an_android_made_again_keeps_its_diff(
    console: Console, layers: tuple[Path, Path]
) -> None:
    _base, diff = layers
    (diff / "telebuba-acc-0").mkdir(parents=True)
    (diff / "telebuba-acc-0" / "signed-in").write_text("", encoding="utf-8")
    console.answer = _docker(gateways="")  # its gateway went: Android is made again

    await pod.start_pod("telebuba-acc-0", _SPEC, _PROXY)

    assert _call(console, "rm", "telebuba-acc-0")
    assert _removals(console) == [], "the account's sign-in stays on disk"
    assert (diff / "telebuba-acc-0" / "signed-in").exists()


@pytest.mark.usefixtures("layers")
@pytest.mark.asyncio
async def test_removing_a_pod_removes_its_diff_after_its_containers(console: Console) -> None:
    console.answer = _docker()

    assert await pod.remove_pod("telebuba-acc-2") is True

    removals = [call for call in console.calls if call[1] in {"rm", "run"}]
    assert [call[-1] for call in removals] == [
        "telebuba-acc-2",
        "telebuba-acc-2-net",
        "/data-diff/telebuba-acc-2",
    ]


@pytest.mark.asyncio
async def test_a_running_pod_needs_no_base_to_start_again(
    console: Console, layers: tuple[Path, Path]
) -> None:
    base, _diff = layers
    (base / str(_SPEC.sdk)).rmdir()
    console.answer = _docker(gateways="0|telebuba-acc-0-net|exited\n")

    await pod.start_pod("telebuba-acc-0", _SPEC, _PROXY)

    assert _call(console, "start", "telebuba-acc-0"), "made before shared /data: its own /data"


@pytest.mark.asyncio
async def test_the_diff_recorded_at_create_is_removed_after_the_setting_moved(
    console: Console, layers: tuple[Path, Path], monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    _base, diff = layers
    console.answer = _docker()
    await pod.start_pod("telebuba-new", _SPEC, _PROXY)
    android = _call(console, "create", "telebuba-new")
    assert f"telebuba.data_diff={(diff / 'telebuba-new').as_posix()}" in android
    moved = tmp_path / "moved"
    monkeypatch.setattr(settings.emulator, "data_diff_dir", moved)

    await pod.remove_pod("telebuba-new")

    mounts = sorted(call[call.index("--volume") + 1] for call in _removals(console))
    assert mounts == sorted([f"{diff.as_posix()}:/data-diff", f"{moved.as_posix()}:/data-diff"])


@pytest.mark.asyncio
async def test_a_recorded_diff_is_removed_with_shared_data_turned_off(
    console: Console, tmp_path: Path
) -> None:
    await _data.remove_diff("telebuba-acc", f"{tmp_path.as_posix()}/old/telebuba-acc")

    assert [call[call.index("--volume") + 1] for call in _removals(console)] == [
        f"{tmp_path.as_posix()}/old:/data-diff"
    ]
    with pytest.raises(EmulatorError):
        await _data.remove_diff("telebuba-acc", "/srv/elsewhere/other-pod")


@pytest.mark.asyncio
async def test_a_root_is_never_mounted_to_remove_a_diff(console: Console) -> None:
    for recorded in ("/telebuba-acc", "//telebuba-acc", "C:/telebuba-acc", "/var/../telebuba-acc"):
        with pytest.raises(EmulatorError):
            await _data.remove_diff("telebuba-acc", recorded)

    assert _removals(console) == []


def _android_start_fails(argv: list[str]) -> bool:
    return argv[1] == "start" and argv[-1] == "telebuba-new"


@pytest.mark.usefixtures("layers")
@pytest.mark.asyncio
async def test_a_failed_start_takes_away_the_diff_it_made(console: Console) -> None:
    console.answer = _docker(fail=_android_start_fails)

    with pytest.raises(EmulatorError):
        await pod.start_pod("telebuba-new", _SPEC, _PROXY)

    assert [call[-1] for call in _removals(console)] == ["/data-diff/telebuba-new"]


@pytest.mark.asyncio
async def test_a_failed_start_keeps_a_diff_it_found(
    console: Console, layers: tuple[Path, Path]
) -> None:
    _base, diff = layers
    (diff / "telebuba-new").mkdir(parents=True)
    console.answer = _docker(fail=_android_start_fails)

    with pytest.raises(EmulatorError):
        await pod.start_pod("telebuba-new", _SPEC, _PROXY)

    assert _removals(console) == [], "an earlier pod's sign-in may live there"


@pytest.mark.asyncio
async def test_a_missing_gpu_leaves_the_android_whose_gateway_went(
    console: Console, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(settings.emulator, "gpu_mode", "host")
    monkeypatch.setattr(host, "_dri_seen", False)
    docker = _docker(gateways="")

    def answer(argv: list[str]) -> str:
        if argv[1] == "run" and "--device" in argv:
            code = "emulator_command_failed"
            raise EmulatorError(code)
        return docker(argv)

    console.answer = answer

    with pytest.raises(EmulatorError) as refused:
        await pod.start_pod("telebuba-acc-0", _SPEC, _PROXY)

    assert refused.value.code == "emulator_gpu_missing"
    assert [call for call in console.calls if call[1] in {"rm", "create"}] == []


def _recorded(old: str) -> Callable[[list[str]], str]:
    """The fake docker, with ``telebuba-acc-0`` once created on the diff ``old``."""
    docker = _docker(gateways="")

    def answer(argv: list[str]) -> str:
        if argv[1] == "inspect" and "telebuba.data_diff" in argv[3] and argv[4] == "telebuba-acc-0":
            return old + "\n"
        return docker(argv)

    return answer


@pytest.mark.usefixtures("layers")
@pytest.mark.asyncio
async def test_an_android_made_again_stays_on_its_recorded_diff(
    console: Console, tmp_path: Path
) -> None:
    old = (tmp_path / "old-diff" / "telebuba-acc-0").as_posix()
    console.answer = _recorded(old)

    await pod.start_pod("telebuba-acc-0", _SPEC, _PROXY)

    android = _call(console, "create", "telebuba-acc-0")
    assert f"{old}:/data-diff" in android, "not the newly configured directory"
    assert f"telebuba.data_diff={old}" in android


@pytest.mark.asyncio
async def test_an_android_made_again_with_shared_data_off_keeps_its_diff_labelled(
    console: Console, tmp_path: Path
) -> None:
    old = (tmp_path / "old-diff" / "telebuba-acc-0").as_posix()
    console.answer = _recorded(old)

    await pod.start_pod("telebuba-acc-0", _SPEC, _PROXY)

    android = _call(console, "create", "telebuba-acc-0")
    assert "--volume" not in android
    assert f"telebuba.data_diff={old}" in android, "deleting the account still finds it"
