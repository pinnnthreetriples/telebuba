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

_GATEWAY = "telebuba/net-gateway:1"


@pytest.fixture(autouse=True)
def _fresh(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(host, "_binder_seen", False)
    monkeypatch.setattr(images, "_LOCKS", {})
    monkeypatch.setattr(settings.emulator, "gateway_image", _GATEWAY)


def _host(
    *,
    refuse: Callable[[list[str]], EmulatorRefusalCode | None] = lambda _argv: None,
    gateway: bool = True,
) -> Callable[[list[str]], str]:
    def answer(argv: list[str]) -> str:
        code = refuse(argv)
        if code is None and argv[1:3] == ["image", "inspect"] and not gateway:
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

    await host.check_host(build_probe=False)
    await host.check_host(build_probe=False)

    assert _probes(console) == [
        [
            str(settings.emulator.docker_path),
            *("run", "--rm", "--entrypoint", "grep", _GATEWAY),
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
        await host.check_host(build_probe=False)

    assert refusal.value.code == expected


@pytest.mark.asyncio
async def test_without_the_gateway_image_the_start_does_not_build_it(console: Console) -> None:
    console.answer = _host(gateway=False)

    await host.check_host(build_probe=False)

    assert _probes(console) == []
    assert not [call for call in console.calls if call[1] == "build"]


@pytest.mark.asyncio
async def test_the_preparation_builds_the_gateway_image_to_probe(console: Console) -> None:
    console.answer = _host(gateway=False)

    await host.check_host(build_probe=True)

    assert [call[1] for call in console.calls if call[1] in {"build", "run"}] == ["build", "run"]
