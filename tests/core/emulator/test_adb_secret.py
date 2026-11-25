"""A password or login code reaches the phone through adb's stdin, never its argv.

The host's process list shows every argv for as long as the process runs, and adb or
process logging may keep it. ``type_secret`` sends the device-side command through the
STDIN of ``adb -s <serial> shell`` instead; these tests pin that the secret is absent
from argv, present in stdin with ``type_text``'s own escaping, and absent from every
log line and exception when the call fails or times out.
"""

from __future__ import annotations

import asyncio
import logging
import random
from typing import TYPE_CHECKING

import pytest

from core.config import settings
from core.emulator import _process, adb
from core.emulator._errors import EmulatorError
from core.emulator._input import HumanInputEngine

if TYPE_CHECKING:
    from tests.core.emulator.conftest import Console

# Spaces, quotes, ``$``, ``&``, ``%``-free (``%`` is refused) and shell-special characters.
_TRICKY = "a b'c\"d$e&f;g|h`i(j)k<l>m\\n"
_SERIAL = "127.0.0.1:5601"


def _engine() -> HumanInputEngine:
    return HumanInputEngine(random.Random(0))  # noqa: S311 - a seeded test rhythm


@pytest.mark.asyncio
async def test_the_secret_is_in_stdin_and_not_in_argv(console: Console) -> None:
    await adb.type_secret(_SERIAL, _TRICKY)

    (argv,) = console.calls
    assert argv == [str(settings.emulator.adb_path), "-s", _SERIAL, "shell"]
    assert not any("$e" in part or "b'c" in part for part in argv)
    assert console.stdins == [f"input text {adb.escape_input_text(_TRICKY)}\n".encode("ascii")]


@pytest.mark.asyncio
async def test_the_escaping_is_the_one_type_text_uses(console: Console) -> None:
    await adb.type_text(_SERIAL, _TRICKY)
    await adb.type_secret(_SERIAL, _TRICKY)

    typed_in_argv = console.shell_calls()[0]
    assert typed_in_argv[:2] == ["input", "text"]
    assert console.stdins[1] == f"{' '.join(typed_in_argv)}\n".encode("ascii")
    assert "%s" in typed_in_argv[2], "a space is still ``%s``"


@pytest.mark.asyncio
async def test_a_secret_input_text_cannot_type_is_refused_before_adb_runs(
    console: Console,
) -> None:
    with pytest.raises(EmulatorError) as refused:
        await adb.type_secret(_SERIAL, "100%пароль")

    assert refused.value.code == "emulator_text_unsupported"
    assert console.calls == []


@pytest.mark.asyncio
async def test_every_character_of_a_secret_goes_through_stdin(
    console: Console, monkeypatch: pytest.MonkeyPatch
) -> None:
    async def _no_sleep(_seconds: float) -> None:
        return None

    monkeypatch.setattr("core.emulator.adb.asyncio.sleep", _no_sleep)

    await adb.human_type(_SERIAL, "s3cr$t", engine=_engine(), secret=True)

    assert not [c for c in console.shell_calls() if c[:2] == ["input", "text"]]
    typed = "".join(s.decode().removeprefix("input text ").strip() for s in console.stdins if s)
    assert typed.replace("\\", "") == "s3cr$t"


@pytest.mark.asyncio
async def test_ordinary_text_still_goes_through_argv(console: Console) -> None:
    await adb.human_type(_SERIAL, "news", engine=_engine(), typo_probability=0.0)

    assert [c for c in console.shell_calls() if c[:2] == ["input", "text"]]
    assert all(stdin is None for stdin in console.stdins)


class _Process:
    """A child that reads its stdin and either exits with ``returncode`` or never answers."""

    def __init__(self, returncode: int, *, hang: bool = False) -> None:
        self.returncode: int | None = None if hang else returncode
        self._exit = returncode
        self._hang = hang
        self.fed: bytes | None = None
        self.killed = False

    async def communicate(self, stdin: bytes | None = None) -> tuple[bytes, bytes]:
        self.fed = stdin
        if self._hang:
            await asyncio.sleep(3600)
        return b"", b"stderr that mentions nothing"

    def kill(self) -> None:
        self.killed = True
        self.returncode = self._exit

    async def wait(self) -> int:
        return self._exit


def _spawn(monkeypatch: pytest.MonkeyPatch, process: _Process) -> list[tuple[str, ...]]:
    argvs: list[tuple[str, ...]] = []

    async def fake(*argv: str, **_kwargs: object) -> _Process:
        argvs.append(argv)
        return process

    monkeypatch.setattr(asyncio, "create_subprocess_exec", fake)
    return argvs


@pytest.mark.asyncio
async def test_a_failed_secret_leaves_no_trace_of_it(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    process = _Process(1)
    argvs = _spawn(monkeypatch, process)
    secret = "Hunter$2 pass"

    with caplog.at_level(logging.DEBUG), pytest.raises(EmulatorError) as failed:
        await adb.type_secret(_SERIAL, secret)

    assert failed.value.code == "emulator_command_failed"
    assert process.fed is not None
    assert b"Hunter" in process.fed
    assert "exited with 1" in caplog.text, "the failure was logged, so the check below means it"
    for text in (caplog.text, str(failed.value), repr(failed.value), *map(str, argvs)):
        assert "Hunter" not in text
        assert "pass" not in text


@pytest.mark.asyncio
async def test_a_secret_that_times_out_is_killed_and_leaves_no_trace_of_it(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    process = _Process(1, hang=True)
    _spawn(monkeypatch, process)
    monkeypatch.setattr(settings.emulator, "command_timeout_seconds", 0.05)

    with caplog.at_level(logging.DEBUG), pytest.raises(EmulatorError) as failed:
        await adb.type_secret(_SERIAL, "Hunter$2")

    assert failed.value.code == "emulator_command_timeout"
    assert process.killed
    assert "timed out" in caplog.text
    assert "Hunter" not in caplog.text + str(failed.value) + repr(failed.value.__cause__)


@pytest.mark.asyncio
async def test_shell_without_arguments_is_a_stdin_script_step(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    steps: list[str] = []

    async def fake(
        argv: list[str], *, seconds: float, step: str, stdin: bytes | None = None
    ) -> str:
        del argv, seconds, stdin
        steps.append(step)
        return ""

    monkeypatch.setattr(_process, "run_command", fake)
    await _process.shell(_SERIAL, stdin=b"true\n")
    await _process.shell(_SERIAL, "getprop", "x")

    assert steps == ["adb shell stdin", "adb shell getprop"]
