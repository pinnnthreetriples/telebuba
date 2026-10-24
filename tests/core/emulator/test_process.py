"""One console command: argv list, bounded by a timeout, killed when abandoned."""

from __future__ import annotations

import asyncio
import sys

import pytest

from core.emulator import _process
from core.emulator._errors import EmulatorError
from core.emulator._process import run_command


@pytest.mark.asyncio
async def test_stdout_is_returned_and_nothing_goes_through_a_shell() -> None:
    out = await run_command(
        [sys.executable, "-c", "import sys; print(sys.argv[1])", "a;b|$(c)"],
        seconds=30,
        step="echo",
    )

    assert out.strip() == "a;b|$(c)", "the argument arrives verbatim: no shell parsed it"


@pytest.mark.asyncio
async def test_a_failing_command_has_its_own_code() -> None:
    with pytest.raises(EmulatorError) as failed:
        await run_command([sys.executable, "-c", "raise SystemExit(3)"], seconds=30, step="fail")

    assert failed.value.code == "emulator_command_failed"


@pytest.mark.asyncio
async def test_a_missing_console_is_not_found(tmp_path) -> None:
    with pytest.raises(EmulatorError) as failed:
        await run_command([str(tmp_path / "docker.exe"), "ps"], seconds=5, step="ps")

    assert failed.value.code == "emulator_not_found"


class _Spawned(list[asyncio.subprocess.Process]):
    """Every child ``run_command`` started; ``seen`` is set once there is one."""

    def __init__(self) -> None:
        super().__init__()
        self.seen = asyncio.Event()


async def _spawned(monkeypatch: pytest.MonkeyPatch) -> _Spawned:
    started = _Spawned()
    real = asyncio.create_subprocess_exec

    async def spy(*args: object, **kwargs: object) -> asyncio.subprocess.Process:
        process = await real(*args, **kwargs)  # ty: ignore[invalid-argument-type]
        started.append(process)
        started.seen.set()
        return process

    monkeypatch.setattr(_process.asyncio, "create_subprocess_exec", spy)
    return started


@pytest.mark.asyncio
async def test_a_command_past_its_timeout_is_killed(monkeypatch: pytest.MonkeyPatch) -> None:
    started = await _spawned(monkeypatch)

    with pytest.raises(EmulatorError) as failed:
        await run_command(
            [sys.executable, "-c", "import time; time.sleep(30)"], seconds=0.5, step="sleep"
        )

    assert failed.value.code == "emulator_command_timeout"
    assert started[0].returncode is not None, "the child is gone, not left running"


@pytest.mark.asyncio
async def test_a_cancelled_caller_kills_the_child(monkeypatch: pytest.MonkeyPatch) -> None:
    started = await _spawned(monkeypatch)
    task = asyncio.create_task(
        run_command(
            [sys.executable, "-c", "import time; time.sleep(30)"], seconds=60, step="sleep"
        ),
    )
    await started.seen.wait()

    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task

    assert started[0].returncode is not None, "an abandoned adb/docker call must not linger"


@pytest.mark.asyncio
async def test_a_console_that_cannot_be_started_is_a_code(monkeypatch: pytest.MonkeyPatch) -> None:
    async def refuse(*_args: object, **_kwargs: object) -> None:
        raise PermissionError

    monkeypatch.setattr(_process.asyncio, "create_subprocess_exec", refuse)

    with pytest.raises(EmulatorError) as failed:
        await run_command(["docker.exe", "ps"], seconds=5, step="ps")

    assert failed.value.code == "emulator_command_failed"


@pytest.mark.asyncio
async def test_stdin_is_fed_to_the_command() -> None:
    out = await run_command(
        [sys.executable, "-c", "import sys; print(len(sys.stdin.buffer.read()))"],
        seconds=30,
        step="count",
        stdin=b"\x00tar\xff",
    )

    assert out.strip() == "5", "bytes arrive as they are, a tar for docker cp -"
