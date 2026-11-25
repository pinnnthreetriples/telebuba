"""Adversarial probes of the pod barrier re-check and of the secret-through-stdin path.

A passing test is a claim that held; ``xfail(strict=True)`` is a reproduced gap.
"""

from __future__ import annotations

import asyncio
import logging
import shutil
import string
import subprocess
from types import SimpleNamespace
from typing import TYPE_CHECKING

import pytest

from core.config import settings
from core.emulator import _process, adb
from core.emulator._errors import EmulatorError
from core.emulator._gateway_rules import ruleset_fault
from core.emulator._text import escape_input_text
from services.accounts import _mobile_guard

if TYPE_CHECKING:
    from tests.core.emulator.conftest import Console

# What ``iptables -S OUTPUT`` prints for the rules docker/net-gateway/net-gateway installs
# (iptables-save normalisation: -d before -o, /32, -m tcp, RELATED,ESTABLISHED).
_REAL_V4 = """\
-P OUTPUT DROP
-A OUTPUT -d 127.0.0.11/32 -j DROP
-A OUTPUT -p tcp -m tcp --dport 853 -j DROP
-A OUTPUT -o lo -j ACCEPT
-A OUTPUT -o tun0 -j ACCEPT
-A OUTPUT -o eth0 -m conntrack --ctstate RELATED,ESTABLISHED --ctdir REPLY -j ACCEPT
-A OUTPUT -d 203.0.113.7/32 -o eth0 -p tcp -m tcp --dport 1080 -j ACCEPT
-A OUTPUT -d 203.0.113.7/32 -o eth0 -p udp -j ACCEPT
"""
_REAL_V6 = "-P OUTPUT DROP\n"
_PROXY = "203.0.113.7"


def test_the_ruleset_the_script_writes_is_not_a_fault() -> None:
    assert ruleset_fault(_REAL_V4, _REAL_V6, _PROXY) is None
    # Same rules with CRLF line ends (a docker CLI on Windows) and with a different
    # ctstate order.
    crlf = _REAL_V4.replace("\n", "\r\n")
    assert ruleset_fault(crlf, _REAL_V6.replace("\n", "\r\n"), _PROXY + "\r\n") is None
    swapped = _REAL_V4.replace("RELATED,ESTABLISHED", "ESTABLISHED,RELATED")
    assert ruleset_fault(swapped, _REAL_V6, _PROXY) is None


@pytest.mark.parametrize(
    "hole",
    [
        "-A OUTPUT -j ACCEPT",
        "-A OUTPUT -o eth0 -j ACCEPT",
        "-A OUTPUT -o eth0 -p tcp -m tcp --dport 443 -j ACCEPT",
        "-A OUTPUT -p udp -m udp --dport 53 -j ACCEPT",
        "-A OUTPUT -s 1.2.3.4/32 -j ACCEPT",
    ],
)
def test_the_obvious_holes_are_seen(hole: str) -> None:
    assert ruleset_fault(_REAL_V4 + hole + "\n", _REAL_V6, _PROXY) == "egress_open"


def test_a_hole_in_front_of_the_real_rules_is_seen_too() -> None:
    first, *rest = _REAL_V4.splitlines()
    ruleset = "\n".join([first, "-A OUTPUT -j ACCEPT", *rest])
    assert ruleset_fault(ruleset, _REAL_V6, _PROXY) == "egress_open"


def test_dport_is_not_taken_for_a_destination() -> None:
    rule = "-A OUTPUT -o eth0 -p tcp -m tcp --dport 22 -j ACCEPT"
    assert ruleset_fault(_REAL_V4 + rule + "\n", _REAL_V6, _PROXY) == "egress_open"


def test_a_negated_tun0_accept_is_a_hole() -> None:
    assert (
        ruleset_fault(_REAL_V4 + "-A OUTPUT ! -o tun0 -j ACCEPT\n", _REAL_V6, _PROXY)
        == "egress_open"
    )


def test_an_accept_in_the_v6_chain_is_a_hole() -> None:
    v6 = "-P OUTPUT DROP\n-A OUTPUT -j ACCEPT\n"
    assert ruleset_fault(_REAL_V4, v6, _PROXY) == "egress_open"


def test_a_jump_to_a_user_chain_is_not_trusted() -> None:
    assert ruleset_fault(_REAL_V4 + "-A OUTPUT -j EVIL\n", _REAL_V6, _PROXY) is not None


def test_an_eth0_accept_to_another_host_is_not_the_proxy() -> None:
    rule = "-A OUTPUT -d 8.8.8.8/32 -o eth0 -j ACCEPT"
    assert ruleset_fault(_REAL_V4 + rule + "\n", _REAL_V6, _PROXY) == "egress_open"


def test_a_destination_hidden_in_a_comment_is_not_a_destination() -> None:
    rule = '-A OUTPUT -o eth0 -m comment --comment "x -d y " -j ACCEPT'
    assert ruleset_fault(_REAL_V4 + rule + "\n", _REAL_V6, _PROXY) == "egress_open"


# ---- secrets through stdin ------------------------------------------------------------

_SH = shutil.which("sh")


@pytest.mark.skipif(_SH is None, reason="needs a POSIX sh")
def test_every_typeable_character_stays_one_literal_word_for_sh() -> None:
    """Whatever the device's sh does with the stdin line, a char never becomes syntax."""
    assert _SH is not None
    for char in string.printable[:-6] + "":  # printable ASCII without whitespace controls
        if char == "%":
            continue
        word = escape_input_text(char)
        probe = subprocess.run(  # noqa: S603 - fixed sh, test-only
            [_SH],
            input=f"printf '%s' {word}\n".encode(),
            capture_output=True,
            timeout=20,
            check=False,
        )
        expected = "%s" if char == " " else char
        assert probe.stdout.decode() == expected, repr(char)
        assert probe.returncode == 0, repr(char)


@pytest.mark.parametrize("bad", ["a\nreboot", "a\rb", "a\x00b", "tab\there", "\n", "é", "日本"])
def test_a_newline_or_nonascii_cannot_reach_the_stdin_script(bad: str) -> None:
    with pytest.raises(EmulatorError) as refused:
        escape_input_text(bad)
    assert refused.value.code == "emulator_text_unsupported"


@pytest.mark.asyncio
async def test_a_secret_with_newline_never_spawns_adb(console: Console) -> None:
    with pytest.raises(EmulatorError):
        await adb.type_secret("127.0.0.1:5601", "pw\nrm -rf /data")
    assert console.calls == []
    assert console.stdins == []


@pytest.mark.asyncio
async def test_a_secret_starting_with_dash_is_one_word_after_input_text(console: Console) -> None:
    await adb.type_secret("127.0.0.1:5601", "--help")
    (stdin,) = console.stdins
    assert stdin == b"input text --help\n"  # one line: one command


@pytest.mark.asyncio
async def test_a_very_long_secret_is_one_line(console: Console) -> None:
    await adb.type_secret("127.0.0.1:5601", "a" * 100_000)
    (stdin,) = console.stdins
    assert stdin is not None
    assert stdin.count(b"\n") == 1
    assert stdin.endswith(b"\n")


class _FakeProcess:
    def __init__(self, returncode: int, *, hang: bool = False) -> None:
        self.returncode: int | None = returncode
        self._hang = hang
        self.fed: bytes | None = None

    async def communicate(self, data: bytes | None = None) -> tuple[bytes, bytes]:
        self.fed = data
        if self._hang:
            self.returncode = None
            await asyncio.sleep(3600)
        return b"", b"adb: error\n"

    def kill(self) -> None:
        self.returncode = -9

    async def wait(self) -> int:
        return self.returncode or 0


@pytest.mark.parametrize("mode", ["fail", "timeout", "spawn_error"])
@pytest.mark.asyncio
async def test_the_secret_is_in_no_log_line_and_no_exception_at_debug(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture, mode: str
) -> None:
    secret = "Zq9!hunter2Secret"
    process = _FakeProcess(1, hang=mode == "timeout")

    async def spawn(*argv: str, **_kw: object) -> _FakeProcess:
        assert secret not in " ".join(argv)
        if mode == "spawn_error":
            raise PermissionError(13, "denied", argv[0])
        return process

    monkeypatch.setattr(_process.asyncio, "create_subprocess_exec", spawn)
    monkeypatch.setattr(settings.emulator, "command_timeout_seconds", 0.05)
    caplog.set_level(logging.DEBUG)

    with pytest.raises(EmulatorError) as failure:
        await adb.type_secret("127.0.0.1:5601", secret)

    assert process.fed in (None, f"input text {escape_input_text(secret)}\n".encode())
    blob = " ".join(
        [caplog.text, str(failure.value), repr(failure.value), repr(failure.value.args)]
    )
    assert "Zq9" not in blob
    assert "hunter2" not in blob
    assert failure.value.__cause__ is None or secret not in repr(failure.value.__cause__)


# ---- the operator's open: pod limit and memory -------------------------------------------


@pytest.mark.asyncio
async def test_a_failed_docker_listing_refuses_the_open_instead_of_counting_zero(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def broken() -> list[object]:
        code = "emulator_command_failed"
        raise EmulatorError(code)

    monkeypatch.setattr(_mobile_guard, "list_instances", broken)
    monkeypatch.setattr(settings.emulator, "max_running_pods", 1)
    with pytest.raises(EmulatorError):
        await _mobile_guard.refuse_if_host_full("telebuba-x")


@pytest.mark.parametrize("state", ["running", "paused"])
@pytest.mark.asyncio
async def test_paused_and_running_pods_both_count_and_own_pod_does_not(
    monkeypatch: pytest.MonkeyPatch, state: str
) -> None:
    other = SimpleNamespace(name="telebuba-o", running=state == "running", paused=state == "paused")
    mine = SimpleNamespace(name="telebuba-x", running=True, paused=False)

    async def listing() -> list[object]:
        return [other, mine]

    async def no_memory() -> int | None:
        return None

    monkeypatch.setattr(_mobile_guard, "list_instances", listing)
    monkeypatch.setattr(_mobile_guard, "host_memory_bytes", no_memory)
    monkeypatch.setattr(settings.emulator, "max_running_pods", 1)
    with pytest.raises(EmulatorError) as refused:
        await _mobile_guard.refuse_if_host_full("telebuba-new")
    assert refused.value.code == "emulator_pod_limit"
    monkeypatch.setattr(settings.emulator, "max_running_pods", 2)
    await _mobile_guard.refuse_if_host_full("telebuba-x")  # one other besides mine: passes
