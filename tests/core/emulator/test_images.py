"""A pod's images are pulled or built once when missing, never when present."""

from __future__ import annotations

import asyncio
from typing import TYPE_CHECKING

import pytest

from core.config import settings
from core.device_fingerprint_android import generate_android_fingerprint
from core.emulator import EmulatorError, images
from core.emulator._profile import container_spec

if TYPE_CHECKING:
    from collections.abc import Callable, Sequence

    from tests.core.emulator.conftest import Console

_SPEC = container_spec(
    generate_android_fingerprint("acc", None).model_copy(
        update={"device_model": "GooglePixel 8", "system_version": "SDK 34"},
    ),
    "DE",
)
_ANDROID = "redroid/redroid:14.0.0_64only-latest"
_GATEWAY = "telebuba/net-gateway:2"


@pytest.fixture(autouse=True)
def _images(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(settings.emulator, "redroid_images", {34: _ANDROID})
    monkeypatch.setattr(settings.emulator, "gateway_image", _GATEWAY)
    monkeypatch.setattr(images, "_LOCKS", {})


def _docker(present: set[str], fail: Callable[[list[str]], bool] = lambda _a: False):
    def answer(argv: list[str]) -> str:
        if argv[1:3] == ["image", "inspect"]:
            if argv[-1] not in present:
                code = "emulator_command_failed"
                raise EmulatorError(code)
            return "sha256:abc\n"
        if fail(argv):
            code = "emulator_command_failed"
            raise EmulatorError(code)
        present.add(argv[3] if argv[1] == "build" else argv[2])
        return ""

    return answer


def _made(console: Console) -> list[list[str]]:
    return [call[1:] for call in console.calls if call[1] in {"pull", "build"}]


@pytest.mark.asyncio
async def test_missing_images_are_built_and_pulled(console: Console) -> None:
    console.answer = _docker(set())

    await images.ensure_pod_images(_SPEC)

    assert _made(console) == [
        ["build", "-t", _GATEWAY, str(images.GATEWAY_CONTEXT)],
        ["pull", _ANDROID],
    ]
    assert (images.GATEWAY_CONTEXT / "Dockerfile").is_file()


@pytest.mark.asyncio
async def test_present_images_are_left_alone(console: Console) -> None:
    console.answer = _docker({_ANDROID, _GATEWAY})

    gateway = await images.ensure_pod_images(_SPEC)

    assert _made(console) == []
    assert gateway == "sha256:abc", "the gateway image's ID, for the pod's stale check"


@pytest.mark.asyncio
async def test_a_failed_pull_has_its_own_code(console: Console) -> None:
    console.answer = _docker({_GATEWAY}, fail=lambda argv: argv[1] == "pull")

    with pytest.raises(EmulatorError) as refused:
        await images.ensure_pod_images(_SPEC)

    assert refused.value.code == "emulator_image_unavailable"


@pytest.mark.asyncio
async def test_a_missing_docker_stays_not_found(console: Console) -> None:
    def answer(_argv: list[str]) -> str:
        code = "emulator_not_found"
        raise EmulatorError(code)

    console.answer = answer

    with pytest.raises(EmulatorError) as refused:
        await images.ensure_pod_images(_SPEC)

    assert refused.value.code == "emulator_not_found"


@pytest.mark.asyncio
async def test_the_build_does_not_wait_for_the_pull(monkeypatch: pytest.MonkeyPatch) -> None:
    pulling = asyncio.Event()
    present: set[str] = set()

    async def docker(argv: Sequence[str], *, seconds: float, step: str, **_stdin: object) -> str:
        del seconds, step
        if argv[1:3] == ["image", "inspect"]:
            if argv[-1] not in present:
                code = "emulator_command_failed"
                raise EmulatorError(code)
            return f"sha256:{argv[-1]}\n"
        if argv[1] == "build":
            await pulling.wait()  # only ends while the pull runs alongside
            present.add(argv[3])
        else:
            pulling.set()
            present.add(argv[2])
        return ""

    monkeypatch.setattr("core.emulator._process.run_command", docker)

    gateway = await asyncio.wait_for(images.ensure_pod_images(_SPEC), timeout=5)

    assert gateway is None, "just built: its ID is read where it is compared"


@pytest.mark.asyncio
async def test_a_failed_build_ends_the_call_and_the_pull_beside_it(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    pulling, pull_cancelled = asyncio.Event(), asyncio.Event()

    async def docker(argv: Sequence[str], *, seconds: float, step: str, **_stdin: object) -> str:
        del seconds, step
        if argv[1:3] == ["image", "inspect"]:
            code = "emulator_command_failed"  # neither image is here
            raise EmulatorError(code)
        if argv[1] == "pull":
            pulling.set()
            try:
                await asyncio.Event().wait()  # a pull that would take its whole timeout
            except asyncio.CancelledError:
                pull_cancelled.set()  # where run_command kills its child
                raise
        await pulling.wait()
        code = "emulator_command_failed"  # the build fails while the pull runs
        raise EmulatorError(code)

    monkeypatch.setattr("core.emulator._process.run_command", docker)

    with pytest.raises(EmulatorError) as refused:
        await asyncio.wait_for(images.ensure_pod_images(_SPEC), timeout=5)

    assert refused.value.code == "emulator_image_unavailable"
    assert pull_cancelled.is_set(), "the pull is cancelled, not waited for"
    assert not images._LOCKS[_ANDROID].locked(), "the next start may pull it again"
