"""Waiting for a gateway's tunnel: polled briefly, failed at once when the gateway exited."""

from __future__ import annotations

from typing import TYPE_CHECKING

import pytest

from core.config import settings
from core.emulator import _gateway
from core.emulator._errors import EmulatorError

if TYPE_CHECKING:
    from collections.abc import Callable

    from tests.core.emulator.conftest import Console

_GATEWAY = "telebuba-acc-0-net"


def _gateway_that(
    *, ready_after: int | None = None, running: bool = True
) -> Callable[[list[str]], str]:
    """A docker whose tunnel probe fails ``ready_after`` times (always when ``None``)."""
    probes = {"n": 0}

    def answer(argv: list[str]) -> str:
        if argv[1] == "inspect":
            return "true\n" if running else "false\n"
        probes["n"] += 1
        if ready_after is None or probes["n"] <= ready_after:
            code = "emulator_command_failed"
            raise EmulatorError(code)
        return ""

    return answer


@pytest.mark.asyncio
async def test_a_gateway_that_exited_fails_at_once_not_at_the_timeout(
    console: Console, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(settings.emulator, "tunnel_timeout_seconds", 3600)
    console.answer = _gateway_that(running=False)

    with pytest.raises(EmulatorError) as failed:
        await _gateway.wait_for_tunnel(_GATEWAY)

    assert failed.value.code == "emulator_command_failed"
    assert [call[1] for call in console.calls] == ["exec", "inspect"]


@pytest.mark.asyncio
async def test_an_inspect_that_fails_too_keeps_the_polling_going(
    console: Console, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(_gateway, "_TUNNEL_POLL_SECONDS", 0.001)
    hiccups = {"left": 2}

    def answer(argv: list[str]) -> str:
        if hiccups["left"] > 0:  # the daemon answers neither the probe nor the inspect
            hiccups["left"] -= argv[1] == "inspect"
            code = "emulator_command_failed"
            raise EmulatorError(code)
        return "true\n" if argv[1] == "inspect" else ""

    console.answer = answer

    await _gateway.wait_for_tunnel(_GATEWAY)

    assert [call[1] for call in console.calls] == ["exec", "inspect"] * 2 + ["exec"]


@pytest.mark.asyncio
async def test_a_running_gateway_is_probed_until_its_tunnel_is_up(
    console: Console, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(_gateway, "_TUNNEL_POLL_SECONDS", 0.001)
    console.answer = _gateway_that(ready_after=3)

    await _gateway.wait_for_tunnel(_GATEWAY)

    assert [call[1] for call in console.calls].count("exec") == 4


@pytest.mark.asyncio
async def test_a_tunnel_that_never_comes_up_times_out(
    console: Console, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(settings.emulator, "tunnel_timeout_seconds", 0.05)
    monkeypatch.setattr(_gateway, "_TUNNEL_POLL_SECONDS", 0.001)
    console.answer = _gateway_that()

    with pytest.raises(EmulatorError) as failed:
        await _gateway.wait_for_tunnel(_GATEWAY)

    assert failed.value.code == "emulator_command_timeout"


@pytest.mark.asyncio
async def test_every_docker_call_is_bounded_by_the_time_left(
    console: Console, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(settings.emulator, "tunnel_timeout_seconds", 5)
    monkeypatch.setattr(settings.emulator, "command_timeout_seconds", 60)
    monkeypatch.setattr(_gateway, "_TUNNEL_POLL_SECONDS", 0.001)
    console.answer = _gateway_that(ready_after=2)

    await _gateway.wait_for_tunnel(_GATEWAY)

    assert console.seconds, "the gateway was probed"
    assert all(seconds <= 5 for seconds in console.seconds), "never the 60 s command bound"
