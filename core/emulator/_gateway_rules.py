"""Reading back the gateway's kill-switch: ``iptables -S OUTPUT`` (v4 and v6) against the script.

A pure function of the two listings and the proxy address the gateway was given. The
rules are what ``docker/net-gateway`` writes: both families drop by default; the v4 chain
then holds DROP rules for Docker's DNS and port 853, and ACCEPT rules for loopback, tun0,
REPLY traffic on eth0 and the proxy's own address on eth0. The v6 chain holds no rules.

Each rule is split into shell-style tokens, so an option's value is never taken for an
option (a ``--comment`` text) and a ``!`` is seen for what it is. An ACCEPT is trusted
only when every option on it is one that can merely narrow it and none is negated: a
negated match widens a rule (``! -o tun0`` is everything but tun0). Whatever else this
function cannot vouch for is an open door: a target other than ACCEPT, DROP or REJECT
(a jump into a chain ``-S OUTPUT`` does not print, ``RETURN``, ``LOG``; the script writes
only ACCEPT and DROP, and a rule it did not write means the chain was edited by hand),
an option it does not know on an ACCEPT, or a line it cannot tokenise.
"""

from __future__ import annotations

import ipaddress
import shlex
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from core.emulator._errors import GatewayFault

_OUTPUT_DROP = "-P OUTPUT DROP"
_TARGETS = frozenset({"ACCEPT", "DROP", "REJECT"})
# Options that take one value; any other dash-led word is read as a flag.
_WITH_VALUE = frozenset(
    {"-A", "-o", "-i", "-d", "-s", "-p", "-m", "-j", "-g"}
    | {"--dport", "--sport", "--dports", "--sports", "--ctstate", "--ctdir", "--comment"}
)
# What an ACCEPT may carry: each of these can only cut the rule down (until negated).
_NARROWING = _WITH_VALUE - {"-A", "-i", "-g"}
# The interfaces whose traffic stays in the pod (loopback) or goes to the proxy (tun0).
_INNER = frozenset({"lo", "tun0"})
_Option = tuple[str, str, bool]


def _options(tokens: list[str]) -> list[_Option]:
    """``(name, value, negated)`` for each option of a rule; the value is ``""`` for a flag."""
    found: list[_Option] = []
    negated = False
    index = 0
    while index < len(tokens):
        word = tokens[index]
        index += 1
        if word == "!":
            negated = True
            continue
        if word.startswith("-"):
            value = ""
            if word in _WITH_VALUE and index < len(tokens):
                value = tokens[index]
                index += 1
            found.append((word, value, negated))
        negated = False
    return found


def _is_proxy(destination: str, address: ipaddress.IPv4Address) -> bool:
    """Whether ``-d`` names exactly the proxy's one address (``/32`` or none, nothing wider)."""
    try:
        return ipaddress.ip_network(destination, strict=False) == ipaddress.ip_network(address)
    except ValueError:
        return False


def _accept_is_open(options: list[_Option], *, v6: bool, address: ipaddress.IPv4Address) -> bool:
    """Whether an ACCEPT lets traffic out anywhere but where the gateway meant."""
    if any(negated or name not in _NARROWING for name, _value, negated in options):
        return True
    outs = [value for name, value, _ in options if name == "-o"]
    if len(outs) != 1:
        return True  # none: every interface; two: not something the script writes
    if outs[0] in _INNER:
        return False
    if v6 or outs[0] != "eth0":
        return True
    if [value for name, value, _ in options if name == "--ctdir"] == ["REPLY"]:
        return False  # an answer on a connection adb opened from outside
    destinations = [value for name, value, _ in options if name == "-d"]
    return len(destinations) != 1 or not _is_proxy(destinations[0], address)


def _chain_is_open(listing: str, *, v6: bool, address: ipaddress.IPv4Address) -> bool:
    for line in listing.splitlines():
        if not line.strip().startswith("-A OUTPUT "):
            continue
        try:
            options = _options(shlex.split(line)[2:])
        except ValueError:
            return True
        targets = [value for name, value, _ in options if name == "-j"]
        if len(targets) != 1 or targets[0] not in _TARGETS or any(n == "-g" for n, _, _ in options):
            return True
        if targets[0] == "ACCEPT" and _accept_is_open(options, v6=v6, address=address):
            return True
    return False


def ruleset_fault(v4: str, v6: str, address: str) -> GatewayFault | None:
    """What is wrong with the kill-switch's rules, if anything.

    ``address`` is the content of the gateway's ``/etc/net-gateway/address``: a text
    that is no IPv4 address leaves nothing to compare the rules with (``kill_switch_unreadable``).
    """
    if any(_OUTPUT_DROP not in (x.strip() for x in listing.splitlines()) for listing in (v4, v6)):
        return "kill_switch_missing"
    try:
        proxy = ipaddress.IPv4Address(address.strip())
    except ValueError:
        return "kill_switch_unreadable"
    if _chain_is_open(v4, v6=False, address=proxy) or _chain_is_open(v6, v6=True, address=proxy):
        return "egress_open"
    return None
