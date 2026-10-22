"""A host that cannot run a phone is refused with the one thing to fix."""

from __future__ import annotations

from typing import TYPE_CHECKING

import pytest

from core.config import settings
from core.emulator import EmulatorError, host, images

if TYPE_CHECKING:
    from collections.abc import Callable

    from schemas.emulator import EmulatorRefusalCode
    from tests.core.emulator.conftest import Console

_PROBE = "alpine:3.20"


@pytest.fixture(autouse=True)
def _fresh(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(host, "_binder_seen", False)
    monkeypatch.setattr(host, "_dri_seen", False)
    monkeypatch.setattr(images, "_LOCKS", {})


def _host(
    *,
    refuse: Callable[[list[str]], EmulatorRefusalCode | None] = lambda _argv: None,
    probe_image: bool = True,
) -> Callable[[list[str]], str]:
    def answer(argv: list[str]) -> str:
        code = refuse(argv)
        if code is None and argv[1:3] == ["image", "inspect"] and not probe_image:
            code = "emulator_command_failed"
        if code is not None:
            raise EmulatorError(code)
        return ""

    return answer


def _probes(console: Console) -> list[list[str]]:
    return [call for call in console.calls if call[1] == "run"]


@pytest.mark.asyncio
async def test_a_capable_host_is_probed_once(console: Console) -> None:
    console.answer = _host()

    await host.check_host()
    await host.check_host()

    assert _probes(console) == [
        [
            str(settings.emulator.docker_path),
            *("run", "--rm", "--entrypoint", "grep", _PROBE),
            *("-qw", "binder", "/proc/filesystems"),
        ]
    ], "a kernel found capable is not asked again"


@pytest.mark.parametrize(
    ("refused", "code", "expected"),
    [
        ("version", "emulator_not_found", "emulator_docker_unavailable"),
        ("version", "emulator_command_failed", "emulator_docker_unavailable"),
        ("adb", "emulator_not_found", "emulator_adb_missing"),
        ("run", "emulator_command_failed", "emulator_binder_missing"),
    ],
)
@pytest.mark.asyncio
async def test_what_the_host_lacks_is_named(
    console: Console, refused: str, code: EmulatorRefusalCode, expected: str
) -> None:
    adb = str(settings.emulator.adb_path)

    def refuse(argv: list[str]) -> EmulatorRefusalCode | None:
        hit = argv[0] == adb if refused == "adb" else argv[1] == refused
        return code if hit else None

    console.answer = _host(refuse=refuse)

    with pytest.raises(EmulatorError) as refusal:
        await host.check_host()

    assert refusal.value.code == expected


@pytest.mark.asyncio
async def test_a_host_that_never_ran_a_phone_is_probed_at_the_first_start(
    console: Console,
) -> None:
    console.answer = _host(probe_image=False)

    await host.check_host()

    made = [call[1:3] for call in console.calls if call[1] in {"pull", "build", "run"}]
    assert made == [["pull", _PROBE], ["run", "--rm"]], "a small pull, never the gateway build"
    pull = next(call for call in console.calls if call[1] == "pull")
    assert console.seconds[console.calls.index(pull)] == host._PROBE_PULL_SECONDS


@pytest.mark.asyncio
async def test_a_kernel_without_binder_refuses_even_a_fresh_host(console: Console) -> None:
    console.answer = _host(
        probe_image=False,
        refuse=lambda argv: "emulator_command_failed" if argv[1] == "run" else None,
    )

    with pytest.raises(EmulatorError) as refusal:
        await host.check_host()

    assert refusal.value.code == "emulator_binder_missing"


@pytest.mark.asyncio
async def test_a_probe_image_that_cannot_be_pulled_refuses_the_start(console: Console) -> None:
    console.answer = _host(
        probe_image=False,
        refuse=lambda argv: "emulator_command_timeout" if argv[1] == "pull" else None,
    )

    with pytest.raises(EmulatorError) as refusal:
        await host.check_host()

    assert refusal.value.code == "emulator_image_unavailable"
    assert not host._binder_seen


def test_the_probe_image_is_the_gateways_own_base() -> None:
    dockerfile = (images.GATEWAY_CONTEXT / "Dockerfile").read_text(encoding="utf-8")
    assert f"FROM {host.PROBE_IMAGE}" in dockerfile.splitlines()


_DRI_PROBE = ["run", "--rm", "--device", "/dev/dri:/dev/dri", "--entrypoint", "true", _PROBE]


def _no_dri(argv: list[str]) -> EmulatorRefusalCode | None:
    return "emulator_command_failed" if "--device" in argv else None


def _dri_probes(console: Console) -> list[list[str]]:
    return [call[1:] for call in _probes(console) if "--device" in call]


@pytest.mark.asyncio
async def test_guest_drawing_never_probes_the_gpu(console: Console) -> None:
    console.answer = _host(refuse=_no_dri)

    await host.check_host()

    assert await host.gpu_mode() == "guest"
    assert _dri_probes(console) == []


@pytest.mark.asyncio
async def test_host_drawing_without_a_gpu_refuses_the_start(
    console: Console, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(settings.emulator, "gpu_mode", "host")
    console.answer = _host(refuse=_no_dri)

    with pytest.raises(EmulatorError) as refusal:
        await host.check_host()

    assert refusal.value.code == "emulator_gpu_missing"
    assert _dri_probes(console) == [_DRI_PROBE]


@pytest.mark.parametrize("mode", ["host", "auto"])
@pytest.mark.asyncio
async def test_a_gpu_found_is_drawn_on_and_not_asked_again(
    console: Console, monkeypatch: pytest.MonkeyPatch, mode: str
) -> None:
    monkeypatch.setattr(settings.emulator, "gpu_mode", mode)
    console.answer = _host()

    await host.check_host()

    assert await host.gpu_mode() == "host"
    assert await host.gpu_mode() == "host"
    assert len(_dri_probes(console)) == 1


@pytest.mark.asyncio
async def test_auto_without_a_gpu_draws_in_software_and_asks_again_later(
    console: Console, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(settings.emulator, "gpu_mode", "auto")
    console.answer = _host(refuse=_no_dri)

    await host.check_host()
    assert await host.gpu_mode() == "guest"
    assert await host.gpu_mode() == "guest"

    assert _dri_probes(console) == [_DRI_PROBE, _DRI_PROBE], "a driver installed later is found"
