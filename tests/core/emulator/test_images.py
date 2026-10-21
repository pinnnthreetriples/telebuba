"""A pod's images are pulled or built once when missing, never when present."""

from __future__ import annotations

from typing import TYPE_CHECKING

import pytest

from core.config import settings
from core.device_fingerprint_android import generate_android_fingerprint
from core.emulator import EmulatorError, images
from core.emulator._profile import container_spec

if TYPE_CHECKING:
    from collections.abc import Callable

    from tests.core.emulator.conftest import Console

_SPEC = container_spec(
    generate_android_fingerprint("acc", None).model_copy(
        update={"device_model": "GooglePixel 8", "system_version": "SDK 34"},
    ),
    "DE",
)
_ANDROID = "redroid/redroid:14.0.0_64only-latest"
_GATEWAY = "telebuba/net-gateway:1"


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

    await images.ensure_pod_images(_SPEC)

    assert _made(console) == []


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
