"""``docker/net-gateway/net-gateway`` under a real ``sh``, its commands faked and logged.

The kill-switch comes before anything is read, any failed rule stops the script before
the tunnel starts, and the proxy's credentials reach the tunnel's config as data.
"""

from __future__ import annotations

import os
import shutil
import subprocess
from pathlib import Path
from typing import NamedTuple

import pytest

_SH = shutil.which("sh")
_SCRIPT = Path(__file__).parents[3] / "docker" / "net-gateway" / "net-gateway"
pytestmark = pytest.mark.skipif(_SH is None, reason="needs a POSIX sh")

# Each fake logs its argv; ``ip`` answers the two eth0 lookups and has no tun0 yet.
_FAKES = {
    "iptables": 'case "$*" in *"$FAIL_ON"*) exit 4 ;; esac',
    "ip6tables": 'case "$*" in *"$FAIL_ON"*) exit 4 ;; esac',
    "ip": (
        'case "$*" in\n'
        '"-4 route show dev eth0 scope link") echo "172.30.0.0/16 proto kernel scope link" ;;\n'
        '"-4 route show default dev eth0") echo "default via 172.30.0.1 dev eth0" ;;\n'
        '"link show tun0") exit 1 ;;\n'
        "esac"
    ),
    "hev-socks5-tunnel": 'cp "$1" "$WORK/hev.yml"',
}


class _Run(NamedTuple):
    status: int
    calls: list[str]
    config: str | None


def _run(
    tmp_path: Path,
    proxy: str | None = "proxy.example\n1080\nuser\npass\n",
    fail_on: str = "never-matches",
) -> _Run:
    conf = tmp_path / "conf"
    conf.mkdir()
    if proxy is not None:
        (conf / "proxy").write_text(proxy, encoding="utf-8", newline="\n")
        (conf / "address").write_text("203.0.113.7\n", encoding="utf-8", newline="\n")
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    log = tmp_path / "calls.log"
    for name, body in _FAKES.items():
        fake = bin_dir / name
        fake.write_text(
            f'#!/bin/sh\necho "{name} $*" >> "$WORK/calls.log"\n{body}\nexit 0\n',
            encoding="utf-8",
            newline="\n",
        )
        fake.chmod(0o755)
    script = tmp_path / "net-gateway"
    script.write_text(
        _SCRIPT.read_text(encoding="utf-8")
        .replace("CONF=/etc/net-gateway", f"CONF={conf.as_posix()}")
        .replace("RUN=/run/net-gateway", f"RUN={(tmp_path / 'run').as_posix()}"),
        encoding="utf-8",
        newline="\n",
    )
    env = {
        **os.environ,
        "PATH": f"{bin_dir}{os.pathsep}{os.environ['PATH']}",
        "WORK": tmp_path.as_posix(),
        "FAIL_ON": fail_on,
    }
    assert _SH is not None
    done = subprocess.run(  # noqa: S603 - test-owned argv
        [_SH, script.as_posix(), "run"], env=env, check=False, timeout=30, capture_output=True
    )
    calls = log.read_text(encoding="utf-8").splitlines() if log.exists() else []
    config = tmp_path / "hev.yml"
    text = config.read_text(encoding="utf-8") if config.exists() else None
    return _Run(done.returncode, calls, text)


def _index(calls: list[str], prefix: str) -> int:
    return next(i for i, call in enumerate(calls) if call.startswith(prefix))


def test_egress_is_dropped_before_the_tunnel_starts_and_the_rules_are_fail_closed(
    tmp_path: Path,
) -> None:
    run = _run(tmp_path)

    assert run.status == 1, "the faked tunnel exits at once, and so does the gateway"
    assert run.calls[0] == "ip6tables -P INPUT ACCEPT", "the kill-switch comes first"
    drop = run.calls.index("iptables -P OUTPUT DROP")
    assert drop < _index(run.calls, "iptables -A OUTPUT") < _index(run.calls, "hev-socks5-tunnel")
    established = next(c for c in run.calls if "ESTABLISHED" in c)
    assert "--ctdir REPLY" in established, "only replies to connections made from outside"
    assert "iptables -A INPUT -i eth0 -p tcp --dport 5555 ! -s 172.30.0.1 -j DROP" in run.calls
    embedded_dns = run.calls.index("iptables -A OUTPUT -d 127.0.0.11 -j DROP")
    assert embedded_dns < run.calls.index("iptables -A OUTPUT -o lo -j ACCEPT")


@pytest.mark.parametrize("step", ["-P OUTPUT DROP", "--ctdir REPLY", "--dport 5555", "TCPMSS"])
def test_a_rule_that_fails_stops_the_gateway_before_the_tunnel(tmp_path: Path, step: str) -> None:
    run = _run(tmp_path, fail_on=step)

    assert run.status != 0
    assert not any(call.startswith("hev-socks5-tunnel") for call in run.calls)


@pytest.mark.parametrize(
    ("proxy", "why"),
    [
        (None, "no proxy file"),
        ("proxy.example\n10x0\nuser\npass\n", "a port that is not a number"),
        ("proxy.example\n\nuser\npass\n", "no port"),
    ],
)
def test_a_proxy_the_gateway_cannot_use_stops_it_with_egress_dropped(
    tmp_path: Path,
    proxy: str | None,
    why: str,
) -> None:
    run = _run(tmp_path, proxy)

    assert run.status == 2, why
    assert "iptables -P OUTPUT DROP" in run.calls, "dropped before the input is read"
    assert not any(call.startswith("hev-socks5-tunnel") for call in run.calls)


def test_credentials_reach_the_tunnel_config_as_quoted_data(tmp_path: Path) -> None:
    password = "it's $(id) `x` #\" \\n"

    run = _run(tmp_path, f"proxy.example\n1080\nus'er\n{password}\n")

    assert run.config is not None
    assert "  address: '203.0.113.7'\n  port: 1080\n" in run.config
    assert "  username: 'us''er'\n" in run.config
    assert f"  password: '{password.replace(chr(39), chr(39) * 2)}'\n" in run.config


def test_without_a_username_the_tunnel_config_has_no_credentials(tmp_path: Path) -> None:
    run = _run(tmp_path, "proxy.example\n1080\n\n\n")

    assert run.config is not None
    assert "username" not in run.config
    assert "password" not in run.config
