"""A pod's gateway made from an image other than the configured one is made again at its start."""

from __future__ import annotations

from typing import TYPE_CHECKING

import pytest

from core.config import settings
from core.emulator import pod
from core.emulator._errors import EmulatorError
from tests.core.emulator.test_pod import _PROXY, _SPEC, _call, _docker, _verbs

if TYPE_CHECKING:
    from collections.abc import Callable, Sequence
    from pathlib import Path

    from tests.core.emulator.conftest import Console


def _older_gateways(
    docker: Callable[[list[str]], str], stale: Sequence[str], *, built: bool = True
) -> Callable[[list[str]], str]:
    """``docker`` with the gateways in ``stale`` made from an older image.

    The configured gateway image is present when ``built``, or once ``build`` made it;
    a gateway made again is made from it.
    """
    older = set(stale)
    present = {"built": built}

    def answer(argv: list[str]) -> str:
        if argv[1] == "build":
            present["built"] = True
        if argv[1] == "rm":
            older.discard(argv[-1])
        if argv[1:3] == ["image", "inspect"] and argv[-1] == settings.emulator.gateway_image:
            if not present["built"]:
                code = "emulator_command_failed"
                raise EmulatorError(code)
        elif argv[1] == "inspect" and argv[3] == "{{.Image}}" and argv[4] in older:
            return "sha256:01d\n"
        return docker(argv)

    return answer


def _image_calls(console: Console) -> list[list[str]]:
    """Each ``build`` and gateway-image comparison, in order."""
    return [
        c[1:]
        for c in console.calls
        if c[1] == "build" or "{{.Image}}" in c or c[1:3] == ["image", "inspect"]
    ]


@pytest.mark.asyncio
async def test_a_gateway_of_an_older_image_is_made_again_with_android_on_its_kept_diff(
    console: Console, tmp_path: Path
) -> None:
    old = (tmp_path / "old-diff" / "telebuba-acc-2").as_posix()
    docker = _older_gateways(_docker(), ["telebuba-acc-2-net"])

    def answer(argv: list[str]) -> str:
        if argv[1] == "inspect" and "telebuba.data_diff" in argv[3] and argv[4] == "telebuba-acc-2":
            return old + "\n"
        return docker(argv)

    console.answer = answer

    instance = await pod.start_pod("telebuba-acc-2", _SPEC, _PROXY)

    removed = [c[-1] for c in console.calls if c[1] == "rm"]
    assert removed == ["telebuba-acc-2", "telebuba-acc-2-net"], "Android out of its namespace first"
    verbs = _verbs(console)
    assert verbs.index("create telebuba-acc-2-net") < verbs.index("create telebuba-acc-2")
    assert _call(console, "create", "telebuba-acc-2-net")[-1] == settings.emulator.gateway_image
    assert f"telebuba.data_diff={old}" in _call(console, "create", "telebuba-acc-2")
    assert instance.index == 2, "the pod keeps its index, so its adb port"


@pytest.mark.asyncio
async def test_a_gateway_of_the_configured_image_is_used_as_it_is(console: Console) -> None:
    console.answer = _docker()

    await pod.start_pod("telebuba-acc-2", _SPEC, _PROXY)

    assert ["inspect", "--format", "{{.Image}}", "telebuba-acc-2-net"] in _image_calls(console)
    assert not any(v.startswith(("create", "rm")) for v in _verbs(console))


@pytest.mark.asyncio
async def test_a_gateway_image_missing_here_is_built_before_gateways_are_compared(
    console: Console,
) -> None:
    console.answer = _older_gateways(_docker(), ["telebuba-acc-2-net"], built=False)

    await pod.start_pod("telebuba-acc-2", _SPEC, _PROXY)

    calls = _image_calls(console)
    built = next(i for i, c in enumerate(calls) if c[0] == "build")
    compared = calls.index(["inspect", "--format", "{{.Image}}", "telebuba-acc-2-net"])
    assert calls[built][1:3] == ["-t", settings.emulator.gateway_image]
    assert built < compared
    assert "create telebuba-acc-2-net" in _verbs(console)


@pytest.mark.asyncio
async def test_a_running_pod_of_an_older_gateway_image_is_only_replaced_at_its_next_start(
    console: Console,
) -> None:
    console.answer = _older_gateways(_docker(), ["telebuba-acc-0-net"])

    await pod.pod_runs_proxy("telebuba-acc-0", _PROXY)
    await pod.pause_pod("telebuba-acc-0")
    await pod.stop_pod("telebuba-acc-0")

    assert _image_calls(console) == []
    assert not any(v.startswith(("create", "rm")) for v in _verbs(console))
