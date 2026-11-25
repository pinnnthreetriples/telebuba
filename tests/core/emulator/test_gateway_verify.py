"""A pod that already exists has its network barrier asked again before it is used.

The creation-time wait (``wait_for_tunnel``) vouches for the moment a gateway started.
``verify_gateway`` re-asks the running one: the tunnel, the kill-switch's rules in both
families and the default route, bounded in time, and a gateway that cannot prove it says
so with a stable reason (never an address or a rule's text).
"""

from __future__ import annotations

import asyncio
from typing import TYPE_CHECKING

import pytest

from core.config import settings
from core.emulator import _gateway, host
from core.emulator._errors import EmulatorError, GatewayUnhealthyError
from core.emulator._gateway_rules import ruleset_fault

if TYPE_CHECKING:
    from collections.abc import Callable

    from tests.core.emulator.conftest import Console

_NAME = "telebuba-acc"
_GATEWAY = "telebuba-acc-net"
_V4 = """\
-P INPUT ACCEPT
-P FORWARD DROP
-P OUTPUT DROP
-A OUTPUT -d 127.0.0.11/32 -j DROP
-A OUTPUT -p tcp -m tcp --dport 853 -j DROP
-A OUTPUT -o lo -j ACCEPT
-A OUTPUT -o tun0 -j ACCEPT
-A OUTPUT -o eth0 -m conntrack --ctstate RELATED,ESTABLISHED --ctdir REPLY -j ACCEPT
-A OUTPUT -d 203.0.113.7/32 -o eth0 -p tcp -m tcp --dport 1080 -j ACCEPT
-A OUTPUT -d 203.0.113.7/32 -o eth0 -p udp -j ACCEPT
"""
_V6 = "-P OUTPUT DROP\n"
_ADDRESS = "203.0.113.7\n"
_ROUTE = "198.51.100.1 dev tun0 table 120 src 198.18.0.1 uid 0 \\    cache\n"


@pytest.fixture(autouse=True)
def _quick(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(_gateway, "_RESTORE_SECONDS", 0.01)
    monkeypatch.setattr(settings.emulator, "gateway_check_seconds", 2.0)


def _gateway_says(
    *,
    ready: str | None = "",
    v4: str | None = _V4,
    v6: str | None = _V6,
    address: str | None = _ADDRESS,
    route: str | None = _ROUTE,
) -> Callable[[list[str]], str]:
    """A gateway answering each probe with its text; ``None`` is a probe that exits non-zero."""
    answers = {"net-gateway": ready, "iptables": v4, "ip6tables": v6, "cat": address, "ip": route}

    def answer(argv: list[str]) -> str:
        assert argv[1:3] == ["exec", _GATEWAY], "only the pod's own gateway is asked"
        reply = answers[argv[3]]
        if reply is None:
            code = "emulator_command_failed"
            raise EmulatorError(code)
        return reply

    return answer


@pytest.mark.asyncio
async def test_an_intact_gateway_passes_with_every_probe_asked(console: Console) -> None:
    console.answer = _gateway_says()

    await _gateway.verify_gateway(_NAME)

    assert [call[3:] for call in console.calls] == [
        ["net-gateway", "ready"],
        ["iptables", "-S", "OUTPUT"],
        ["ip6tables", "-S", "OUTPUT"],
        ["cat", "/etc/net-gateway/address"],
        ["ip", "-4", "route", "get", "198.51.100.1"],
    ]
    assert all(0 < seconds <= 2.0 for seconds in console.seconds), "every call is bounded"


@pytest.mark.parametrize(
    ("says", "reason"),
    [
        ({"ready": None}, "tunnel_down"),
        ({"v4": None}, "kill_switch_unreadable"),
        ({"v6": None}, "kill_switch_unreadable"),
        ({"address": None}, "kill_switch_unreadable"),
        ({"address": "not an address\n"}, "kill_switch_unreadable"),
        ({"v4": _V4.replace("-P OUTPUT DROP", "-P OUTPUT ACCEPT")}, "kill_switch_missing"),
        ({"v4": ""}, "kill_switch_missing"),
        ({"v6": "-P OUTPUT ACCEPT\n"}, "kill_switch_missing"),
        ({"v4": _V4 + "-A OUTPUT -j ACCEPT\n"}, "egress_open"),
        ({"v4": _V4 + "-A OUTPUT -o eth0 -p tcp -j ACCEPT\n"}, "egress_open"),
        ({"v6": _V6 + "-A OUTPUT -j ACCEPT\n"}, "egress_open"),
        ({"address": "203.0.113.8\n"}, "egress_open"),
        ({"route": "198.51.100.1 via 172.19.0.1 dev eth0 src 172.19.0.2\n"}, "route_not_tunnel"),
        ({"route": None}, "route_not_tunnel"),
    ],
    ids=[
        "tunnel",
        "v4_unreadable",
        "v6_unreadable",
        "address_unreadable",
        "address_garbage",
        "v4_policy",
        "v4_empty",
        "v6_policy",
        "bare_accept",
        "eth0_accept",
        "v6_accept",
        "other_proxy_address",
        "route_eth0",
        "route_failed",
    ],
)
@pytest.mark.asyncio
async def test_a_barrier_that_is_not_intact_fails_with_its_reason(
    console: Console, says: dict[str, str | None], reason: str
) -> None:
    console.answer = _gateway_says(**says)

    with pytest.raises(GatewayUnhealthyError) as unhealthy:
        await _gateway.verify_gateway(_NAME)

    assert unhealthy.value.code == "emulator_gateway_unhealthy"
    assert unhealthy.value.reason == reason
    assert str(unhealthy.value) == "emulator_gateway_unhealthy", "no address, rule or output"


@pytest.mark.asyncio
async def test_a_routing_flicker_is_asked_once_more_and_a_dead_tunnel_is_not_forgiven(
    console: Console,
) -> None:
    flickers = {"left": 1}
    healthy = _gateway_says()

    def flickering(argv: list[str]) -> str:
        if argv[3] == "net-gateway" and flickers["left"]:
            flickers["left"] -= 1
            code = "emulator_command_failed"
            raise EmulatorError(code)
        return healthy(argv)

    console.answer = flickering
    await _gateway.verify_gateway(_NAME)  # netd flushed the rules; the gateway put them back
    assert flickers["left"] == 0

    console.answer = _gateway_says(ready=None)
    console.calls.clear()
    with pytest.raises(GatewayUnhealthyError):
        await _gateway.verify_gateway(_NAME)
    assert sum(call[3] == "net-gateway" for call in console.calls) == 2, "twice, no more"


@pytest.mark.asyncio
async def test_the_kill_switch_is_never_retried(console: Console) -> None:
    console.answer = _gateway_says(v4=_V4 + "-A OUTPUT -j ACCEPT\n")

    with pytest.raises(GatewayUnhealthyError):
        await _gateway.verify_gateway(_NAME)

    assert sum(call[3] == "net-gateway" for call in console.calls) == 1


@pytest.mark.asyncio
async def test_a_docker_that_times_out_fails_closed(console: Console) -> None:
    def times_out(_argv: list[str]) -> str:
        code = "emulator_command_timeout"
        raise EmulatorError(code)

    console.answer = times_out

    with pytest.raises(GatewayUnhealthyError) as unhealthy:
        await _gateway.verify_gateway(_NAME)

    assert unhealthy.value.reason == "check_timeout"


@pytest.mark.asyncio
async def test_a_hung_docker_exec_cannot_hang_the_check(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(settings.emulator, "gateway_check_seconds", 0.05)

    async def hangs(*_args: str, **_kwargs: object) -> str:
        await asyncio.sleep(3600)
        return ""

    monkeypatch.setattr(_gateway, "docker_or_none", hangs)

    async with asyncio.timeout(5):
        with pytest.raises(GatewayUnhealthyError) as unhealthy:
            await _gateway.verify_gateway(_NAME)

    assert unhealthy.value.reason == "check_timeout"


@pytest.mark.asyncio
async def test_a_missing_docker_is_not_a_barrier_verdict(console: Console) -> None:
    def missing(_argv: list[str]) -> str:
        code = "emulator_not_found"
        raise EmulatorError(code)

    console.answer = missing

    with pytest.raises(EmulatorError) as refused:
        await _gateway.verify_gateway(_NAME)

    assert not isinstance(refused.value, GatewayUnhealthyError)
    assert refused.value.code == "emulator_not_found"


@pytest.mark.asyncio
async def test_the_creation_wait_and_the_recheck_share_one_tunnel_probe(
    console: Console,
) -> None:
    console.answer = _gateway_says()

    await _gateway.wait_for_tunnel(_GATEWAY)
    await _gateway.verify_gateway(_NAME)

    probes = [call[1:] for call in console.calls if call[3:5] == ["net-gateway", "ready"]]
    assert probes == [["exec", _GATEWAY, "net-gateway", "ready"]] * 2


@pytest.mark.parametrize(
    ("reply", "expected"),
    [("16777216000\n", 16777216000), ("garbage", None), ("", None), ("0\n", None), ("-5", None)],
)
@pytest.mark.asyncio
async def test_the_host_memory_is_read_from_docker_info(
    console: Console, reply: str, expected: int | None
) -> None:
    console.answer = lambda _argv: reply

    assert await host.host_memory_bytes() == expected
    assert console.calls[0][1:] == ["info", "--format", "{{.MemTotal}}"]


@pytest.mark.parametrize("code", ["emulator_command_failed", "emulator_not_found"])
@pytest.mark.asyncio
async def test_a_host_that_cannot_say_its_memory_is_none(console: Console, code: str) -> None:
    def refuses(_argv: list[str]) -> str:
        raise EmulatorError(code)  # ty: ignore[invalid-argument-type]

    console.answer = refuses

    assert await host.host_memory_bytes() is None


_PROXY_RULE = "-d 203.0.113.7/32 -o eth0"


@pytest.mark.parametrize(
    "rule",
    [
        f"-A OUTPUT ! {_PROXY_RULE} -j ACCEPT",
        "-A OUTPUT -d 0.0.0.0/0 -o eth0 -j ACCEPT",
        "-A OUTPUT -d 203.0.113.0/24 -o eth0 -j ACCEPT",
        "-A OUTPUT -d 203.0.113.7/32,8.8.8.8/32 -o eth0 -j ACCEPT",
        "-A OUTPUT ! -d 203.0.113.7/32 -o eth0 -j ACCEPT",
        "-A OUTPUT -o eth0 ! --ctdir REPLY -j ACCEPT",
        "-A OUTPUT -o eth0 --ctdir ORIGINAL -j ACCEPT",
        "-A OUTPUT -o tun0 -m unheard --of-match 1 -j ACCEPT",
        f"-A OUTPUT {_PROXY_RULE} -g ACCEPT",
        "-A OUTPUT -j EVIL",
        "-A OUTPUT -j RETURN",
        "-A OUTPUT -o tun0",
        '-A OUTPUT -o tun0 -m comment --comment "unbalanced -j ACCEPT',
        "-A OUTPUT -o wlan0 -j ACCEPT",
        "-A OUTPUT -o eth+ -j ACCEPT",
    ],
)
def test_a_rule_the_script_did_not_write_and_that_can_open_egress_is_a_fault(rule: str) -> None:
    assert ruleset_fault(_V4 + rule + "\n", _V6, _ADDRESS) == "egress_open"


@pytest.mark.parametrize(
    "rule",
    [
        '-A OUTPUT -o tun0 -m comment --comment "-d 8.8.8.8 -j ACCEPT" -j DROP',
        "-A OUTPUT -o eth0 -d 203.0.113.7 -p tcp -m tcp --dport 1080 -j ACCEPT",
        '-A OUTPUT -o eth0 -d 203.0.113.7/32 -m comment --comment "proxy -d x" -j ACCEPT',
        "-A OUTPUT -p udp -m udp --dport 53 -j REJECT --reject-with icmp-port-unreachable",
    ],
)
def test_rules_that_only_narrow_or_close_are_no_fault(rule: str) -> None:
    assert ruleset_fault(_V4 + rule + "\n", _V6, _ADDRESS) is None


@pytest.mark.parametrize(
    ("rule", "fault"),
    [
        ("-A OUTPUT -o lo -j ACCEPT", None),
        ("-A OUTPUT -o tun0 -j ACCEPT", None),
        ("-A OUTPUT -o eth0 -j ACCEPT", "egress_open"),
        ("-A OUTPUT -d 2001:db8::1/128 -o eth0 -j ACCEPT", "egress_open"),
        ("-A OUTPUT ! -o lo -j ACCEPT", "egress_open"),
        ("-A OUTPUT -j USERCHAIN", "egress_open"),
    ],
)
def test_the_v6_chain_allows_only_loopback_and_tun0(rule: str, fault: str | None) -> None:
    assert ruleset_fault(_V4, _V6 + rule + "\n", _ADDRESS) == fault
