"""The pod: a net-gateway and Android in its namespace, started, stopped and removed in order."""

from __future__ import annotations

import asyncio
import hashlib
from pathlib import Path
from typing import TYPE_CHECKING

import pytest

from core.config import settings
from core.device_fingerprint_android import generate_android_fingerprint
from core.emulator import _gateway, host, pod
from core.emulator._errors import EmulatorError
from core.emulator._profile import container_spec
from schemas.proxy import ProxySettings

if TYPE_CHECKING:
    from collections.abc import Callable, Sequence

    from tests.core.emulator.conftest import Console

_SPEC = container_spec(
    generate_android_fingerprint("acc", None).model_copy(
        update={"device_model": "GooglePixel 8", "system_version": "SDK 34"},
    ),
    "DE",
)
_PROXY = ProxySettings(
    proxy_type="socks5", host="203.0.113.7", port=1080, username="tb-user", password="s3cr'et pw"
)
_PROXY_TEXT = "203.0.113.7\n1080\ntb-user\ns3cr'et pw\n"
_ANDROIDS = "0|telebuba-acc-0|running\n2|telebuba-acc-2|exited\n"
_GATEWAYS = "0|telebuba-acc-0-net|running\n2|telebuba-acc-2-net|exited\n"
_GATEWAY_STARTED = "2026-10-17T08:00:00.1Z"
_ANDROID_STARTED = "2026-10-17T08:00:04.123456789Z"


def _docker(
    androids: str = _ANDROIDS,
    gateways: str = _GATEWAYS,
    fail: Callable[[list[str]], bool] = lambda _argv: False,
    *,
    network: bool = True,
    gateway_started: str = _GATEWAY_STARTED,
) -> Callable[[list[str]], str]:
    """A docker holding ``androids`` / ``gateways`` (``ps`` lines) that fails what ``fail`` picks.

    ``create`` adds a container to its list and ``rm`` takes it out, so a pod it made
    is found again by the next listing. The gateways' network exists when ``network``
    (or once created); ``inspect`` answers each gateway's and Android's start time.
    """
    lists = {"android": androids.splitlines(), "gateway": gateways.splitlines()}
    networks = {"exists": network}

    def answer(argv: list[str]) -> str:
        if fail(argv):
            code = "emulator_command_failed"
            raise EmulatorError(code)
        if argv[1:3] == ["network", "inspect"] and not networks["exists"]:
            code = "emulator_command_failed"
            raise EmulatorError(code)
        if argv[1:3] == ["network", "create"]:
            networks["exists"] = True
        if argv[1] == "inspect":
            return "".join(
                f"{gateway_started if name.endswith('-net') else _ANDROID_STARTED}\n"
                for name in argv[4:]
            )
        if argv[1] == "ps" and argv[4] == "label=telebuba.index":
            # One listing of both kinds: the gateway label field is set on gateways only.
            return "".join(f"{line}|\n" for line in lists["android"]) + "".join(
                f"{line}|1\n" for line in lists["gateway"]
            )
        if argv[1] == "ps":
            role = "gateway" if argv[4] == "label=telebuba.gateway" else "android"
            return "".join(f"{line}\n" for line in lists[role])
        _track(lists, argv)
        return ""

    return answer


def _track(lists: dict[str, list[str]], argv: list[str]) -> None:
    """What ``create`` / ``cp`` / ``rm`` do to the fake docker's containers."""
    if argv[1] == "create":
        role = "gateway" if "telebuba.gateway=1" in argv else "android"
        index = next(a for a in argv if a.startswith("telebuba.index=")).split("=")[1]
        lists[role].append(f"{index}|{argv[argv.index('--name') + 1]}|created")
    if argv[1] == "cp" and argv[2].endswith(":/product/etc/build.prop"):
        Path(argv[3]).write_text("ro.product.model=redroid\n", encoding="utf-8")
    if argv[1] == "rm":
        for role, lines in lists.items():
            lists[role] = [line for line in lines if line.split("|")[1] != argv[-1]]


def _verbs(console: Console) -> list[str]:
    """Each docker call as ``verb target`` (listings and the network left out)."""
    docker = str(settings.emulator.docker_path)
    targets = {"start": -1, "stop": -1, "create": 3}
    return [
        f"{call[1]} {call[targets.get(call[1], 2)]}"
        for call in console.calls
        if call[0] == docker and call[1] not in {"ps", "network", "inspect", "image"}
    ]


def _call(console: Console, verb: str, target: str) -> list[str]:
    return next(c for c in console.calls if c[1] == verb and target in c)


def _identity_written(console: Console, name: str) -> list[str]:
    """The ``docker cp`` that writes the phone's ``build.prop`` back into ``name``."""
    target = f"{name}:/product/etc/build.prop"
    return next(c for c in console.calls if c[1] == "cp" and c[3] == target)


@pytest.mark.asyncio
async def test_a_new_pod_starts_its_gateway_and_tunnel_before_android(console: Console) -> None:
    copied: list[tuple[Path, str, str]] = []
    docker = _docker()

    def answer(argv: list[str]) -> str:
        if argv[1] == "cp" and argv[3].endswith(":/etc/"):  # read while the scratch exists
            config = Path(argv[2])
            proxy = (config / "proxy").read_text(encoding="utf-8")
            copied.append((config, proxy, (config / "address").read_text(encoding="utf-8")))
        return docker(argv)

    console.answer = answer

    instance = await pod.start_pod("telebuba-new", _SPEC, _PROXY)

    assert instance == (1, "telebuba-new", True, False), "the lowest index free in both lists"
    assert _verbs(console) == [
        "create telebuba-new-net",
        "cp " + str(copied[0][0]),
        "start telebuba-new-net",
        "exec telebuba-new-net",
        "create telebuba-new",
        "cp telebuba-new:/product/etc/build.prop",
        "cp " + _identity_written(console, "telebuba-new")[2],
        "start telebuba-new",
    ]
    gateway = _call(console, "create", "telebuba-new-net")
    assert gateway[gateway.index("--publish") + 1] == "127.0.0.1:5601:5555", "adb, loopback"
    assert gateway[-1] == settings.emulator.gateway_image
    assert {"NET_ADMIN", "/dev/net/tun", "net.ipv6.conf.all.disable_ipv6=1"} <= set(gateway)
    assert gateway[gateway.index("--network") + 1] == "telebuba-emulator"
    assert gateway[gateway.index("--tmpfs") + 1] == "/run", "the rendered config stays in RAM"
    assert "telebuba.gateway=1" in gateway
    assert "telebuba.index=1" in gateway
    android = _call(console, "create", "telebuba-new")
    assert android[android.index("--network") + 1] == "container:telebuba-new-net"
    assert "telebuba.index=1" in android
    assert _call(console, "exec", "telebuba-new-net")[2:] == [
        "telebuba-new-net",
        "net-gateway",
        "ready",
    ]
    assert console.calls[-1][1:] == ["disconnect", "127.0.0.1:5601"]
    config, proxy, address = copied[0]
    assert (proxy, address) == (_PROXY_TEXT, "203.0.113.7\n")
    assert not config.exists(), "the host-side copy is deleted"


@pytest.mark.asyncio
async def test_a_new_android_is_drawn_as_the_host_allows(
    console: Console, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(settings.emulator, "gpu_mode", "auto")
    monkeypatch.setattr(host, "_dri_seen", False)
    console.answer = _docker()

    await pod.start_pod("telebuba-new", _SPEC, _PROXY)

    android = _call(console, "create", "telebuba-new")
    assert android[android.index("--device") + 1] == "/dev/dri:/dev/dri"
    assert android[-1] == "androidboot.redroid_gpu_mode=host"


@pytest.mark.asyncio
async def test_the_proxys_credentials_never_reach_argv(console: Console) -> None:
    console.answer = _docker()

    await pod.start_pod("telebuba-new", _SPEC, _PROXY)

    argv = "\n".join(" ".join(call) for call in console.calls)
    assert "tb-user" not in argv
    assert "s3cr" not in argv


@pytest.mark.asyncio
async def test_a_proxy_host_name_is_resolved_on_the_host(
    console: Console,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def resolve(host: str) -> str:
        assert host == "proxy.example"
        return "198.51.100.4"

    monkeypatch.setattr(pod, "resolve_address", resolve)
    addresses: list[str] = []
    docker = _docker()

    def answer(argv: list[str]) -> str:
        if argv[1] == "cp" and argv[3].endswith(":/etc/"):
            addresses.append((Path(argv[2]) / "address").read_text(encoding="utf-8"))
        return docker(argv)

    console.answer = answer

    await pod.start_pod("telebuba-new", _SPEC, _PROXY.model_copy(update={"host": "proxy.example"}))

    assert addresses == ["198.51.100.4\n"]


@pytest.mark.asyncio
async def test_a_running_pod_is_stopped_android_first_then_restarted_on_the_proxy(
    console: Console,
) -> None:
    console.answer = _docker()

    instance = await pod.start_pod("telebuba-acc-0", _SPEC, _PROXY)

    assert instance == (0, "telebuba-acc-0", True, False)
    verbs = _verbs(console)
    assert verbs[:2] == ["stop telebuba-acc-0", "stop telebuba-acc-0-net"]
    assert verbs[2].startswith("cp ")
    assert verbs[3:] == [
        "start telebuba-acc-0-net",
        "exec telebuba-acc-0-net",
        "cp telebuba-acc-0:/product/etc/build.prop",
        "cp " + _identity_written(console, "telebuba-acc-0")[2],
        "start telebuba-acc-0",
    ], "its phone is written again before it boots: the proxy may have moved country"


@pytest.mark.asyncio
async def test_an_android_whose_gateway_is_gone_is_made_again_in_a_new_one(
    console: Console,
) -> None:
    console.answer = _docker(gateways="")

    instance = await pod.start_pod("telebuba-acc-2", _SPEC, _PROXY)

    verbs = _verbs(console)
    assert verbs[:2] == ["rm --force", "create telebuba-acc-2-net"]
    assert _call(console, "rm", "telebuba-acc-2")[2:] == ["--force", "telebuba-acc-2"]
    assert "create telebuba-acc-2" in verbs
    assert instance.index == 1


@pytest.mark.parametrize(
    "proxy",
    [
        _PROXY.model_copy(update={"proxy_type": "https"}),
        _PROXY.model_copy(update={"password": "line\nbreak"}),
        _PROXY.model_copy(update={"host": "2001:db8::1"}),
    ],
)
@pytest.mark.asyncio
async def test_a_proxy_the_tunnel_cannot_use_is_refused_before_anything(
    console: Console,
    proxy: ProxySettings,
) -> None:
    with pytest.raises(EmulatorError) as refused:
        await pod.start_pod("telebuba-new", _SPEC, proxy)

    assert refused.value.code == "emulator_proxy_unsupported"
    assert console.calls == []


@pytest.mark.asyncio
async def test_an_sdk_without_an_image_is_refused_before_anything(console: Console) -> None:
    with pytest.raises(EmulatorError) as refused:
        await pod.start_pod("telebuba-new", _SPEC._replace(sdk=31), _PROXY)

    assert refused.value.code == "emulator_sdk_mismatch"
    assert console.calls == []


@pytest.mark.asyncio
async def test_a_tunnel_that_never_comes_up_removes_the_gateway_it_created(
    console: Console,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(settings.emulator, "tunnel_timeout_seconds", 2.5)
    monkeypatch.setattr(settings.emulator, "poll_seconds", 1.0)
    console.answer = _docker(fail=lambda argv: argv[1] == "exec")
    loop = asyncio.get_running_loop()
    clock = {"now": loop.time()}

    async def sleep(seconds: float) -> None:  # only the poll moves the fake clock
        clock["now"] += seconds

    with monkeypatch.context() as frozen:
        frozen.setattr(_gateway.asyncio, "sleep", sleep)
        frozen.setattr(loop, "time", lambda: clock["now"])
        with pytest.raises(EmulatorError) as failed:
            await pod.start_pod("telebuba-new", _SPEC, _PROXY)

    assert failed.value.code == "emulator_command_timeout"
    probes = [call for call in console.calls if call[1] == "exec"]
    assert len(probes) == 4, "polled at 0, 1, 2 and 3 s, not given up on the first failure"
    assert _verbs(console)[-1] == "rm --force"
    assert console.calls[-1][2:] == ["--force", "telebuba-new-net"]
    assert "create telebuba-new" not in _verbs(console), "Android is never created"


@pytest.mark.asyncio
async def test_a_failed_start_stops_what_it_found_and_removes_what_it_created(
    console: Console,
) -> None:
    console.answer = _docker(
        androids="0|telebuba-acc-0|running\n",
        gateways="0|telebuba-acc-0-net|running\n3|telebuba-new-net|exited\n",
        fail=lambda argv: argv[1] == "cp" and argv[3].endswith(":/product/etc/build.prop"),
    )

    with pytest.raises(EmulatorError):
        await pod.start_pod("telebuba-new", _SPEC, _PROXY)

    assert _verbs(console)[-2:] == ["rm --force", "stop telebuba-new-net"]
    assert console.calls[-2][2:] == ["--force", "telebuba-new"]


@pytest.mark.asyncio
async def test_a_probe_that_fails_while_the_gateway_comes_up_is_polled_again(
    console: Console,
) -> None:
    left = {"n": 2}

    def flaky(argv: list[str]) -> bool:
        if argv[1] == "exec" and left["n"] > 0:
            left["n"] -= 1
            return True
        return False

    console.answer = _docker(fail=flaky)

    await _gateway.wait_for_tunnel("telebuba-acc-0-net")

    assert len(console.calls) == 3


@pytest.mark.asyncio
async def test_a_timed_out_probe_is_not_polled_again(console: Console) -> None:
    def timeout(_argv: list[str]) -> str:
        code = "emulator_command_timeout"
        raise EmulatorError(code)

    console.answer = timeout

    with pytest.raises(EmulatorError):
        await _gateway.wait_for_tunnel("telebuba-acc-0-net")

    assert len(console.calls) == 1


@pytest.mark.parametrize(
    ("gateways", "listing", "runs"),
    [
        (_GATEWAYS, hashlib.sha256(_PROXY_TEXT.encode()).hexdigest() + "  /etc/x\n", True),
        (_GATEWAYS, hashlib.sha256(b"other\n").hexdigest() + "  /etc/x\n", False),
        ("0|telebuba-acc-0-net|exited\n", "", False),
        ("", "", False),
    ],
)
@pytest.mark.asyncio
async def test_a_pod_runs_a_proxy_only_while_its_gateway_was_given_it(
    console: Console,
    gateways: str,
    listing: str,
    runs: bool,  # noqa: FBT001 - a parametrised expectation
) -> None:
    listed = _docker(gateways=gateways)
    console.answer = lambda argv: listing if argv[1] == "exec" else listed(argv)

    assert await pod.pod_runs_proxy("telebuba-acc-0", _PROXY) is runs
    execs = [call for call in console.calls if call[1] == "exec"]
    assert all(call[3:] == ["sha256sum", "/etc/net-gateway/proxy"] for call in execs)


@pytest.mark.asyncio
async def test_android_left_in_a_restarted_gateways_old_namespace_is_not_running_it(
    console: Console,
) -> None:
    listed = _docker(gateway_started="2026-10-17T08:00:09Z")
    console.answer = lambda argv: (
        hashlib.sha256(_PROXY_TEXT.encode()).hexdigest() if argv[1] == "exec" else listed(argv)
    )

    assert await pod.pod_runs_proxy("telebuba-acc-0", _PROXY) is False
    assert not [call for call in console.calls if call[1] == "exec"]


@pytest.mark.asyncio
async def test_a_stopped_android_does_not_run_the_proxy(console: Console) -> None:
    console.answer = _docker(androids="0|telebuba-acc-0|exited\n")

    assert await pod.pod_runs_proxy("telebuba-acc-0", _PROXY) is False


@pytest.mark.asyncio
async def test_the_gateways_network_is_made_with_containers_isolated_when_missing(
    console: Console,
) -> None:
    console.answer = _docker(network=False)

    await pod.start_pod("telebuba-new", _SPEC, _PROXY)

    networks = [call[1:] for call in console.calls if call[1] == "network"]
    assert networks[:2] == [
        ["network", "inspect", "telebuba-emulator"],
        [
            "network",
            "create",
            "--driver",
            "bridge",
            "--opt",
            "com.docker.network.bridge.enable_icc=false",
            "telebuba-emulator",
        ],
    ]
    made = next(i for i, c in enumerate(console.calls) if c[1:3] == ["network", "create"])
    assert made < console.calls.index(_call(console, "create", "telebuba-new-net"))


@pytest.mark.asyncio
async def test_a_network_another_start_made_first_is_used(console: Console) -> None:
    tries = {"inspect": 0}
    docker = _docker()

    def answer(argv: list[str]) -> str:
        if argv[1:3] == ["network", "inspect"]:
            tries["inspect"] += 1
            if tries["inspect"] == 1:
                code = "emulator_command_failed"
                raise EmulatorError(code)
        if argv[1:3] == ["network", "create"]:
            code = "emulator_command_failed"
            raise EmulatorError(code)
        return docker(argv)

    console.answer = answer

    await pod.start_pod("telebuba-new", _SPEC, _PROXY)

    assert tries["inspect"] == 2


@pytest.mark.asyncio
async def test_two_pods_started_at_once_take_different_indexes(
    console: Console,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    console.answer = _docker(androids="", gateways="")

    async def yielding(argv: Sequence[str], *, seconds: float, step: str) -> str:
        await asyncio.sleep(0)  # each docker call lets the other start run
        return await console(argv, seconds=seconds, step=step)

    monkeypatch.setattr("core.emulator._process.run_command", yielding)

    first, second = await asyncio.gather(
        pod.start_pod("telebuba-a", _SPEC, _PROXY),
        pod.start_pod("telebuba-b", _SPEC, _PROXY),
    )

    assert {first.index, second.index} == {0, 1}


@pytest.mark.asyncio
async def test_a_proxy_that_cannot_be_copied_in_removes_the_new_gateway(
    console: Console,
) -> None:
    console.answer = _docker(fail=lambda argv: argv[1] == "cp" and argv[3].endswith(":/etc/"))

    with pytest.raises(EmulatorError):
        await pod.start_pod("telebuba-new", _SPEC, _PROXY)

    assert _verbs(console)[-1] == "rm --force"
    assert console.calls[-1][2:] == ["--force", "telebuba-new-net"]
    assert "start telebuba-new-net" not in _verbs(console)


@pytest.mark.asyncio
async def test_a_start_cancelled_midway_still_undoes_itself(
    console: Console,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(settings.emulator, "tunnel_timeout_seconds", 60.0)
    probing = asyncio.Event()
    docker = _docker()

    def answer(argv: list[str]) -> str:
        if argv[1] == "exec":
            probing.set()
            code = "emulator_command_failed"
            raise EmulatorError(code)
        return docker(argv)

    console.answer = answer
    start = asyncio.create_task(pod.start_pod("telebuba-new", _SPEC, _PROXY))
    await probing.wait()

    start.cancel()
    with pytest.raises(asyncio.CancelledError):
        await start

    assert console.calls[-1][1:] == ["rm", "--force", "telebuba-new-net"]


@pytest.mark.asyncio
async def test_a_gateway_that_cannot_answer_does_not_run_the_proxy(console: Console) -> None:
    console.answer = _docker(fail=lambda argv: argv[1] == "exec")

    assert await pod.pod_runs_proxy("telebuba-acc-0", _PROXY) is False


@pytest.mark.asyncio
async def test_stopping_a_pod_stops_android_then_its_gateway(console: Console) -> None:
    console.answer = _docker()

    await pod.stop_pod("telebuba-acc-0")
    await pod.stop_pod("telebuba-acc-2")

    assert _verbs(console) == ["stop telebuba-acc-0", "stop telebuba-acc-0-net"]
    grace = ["--time", str(pod.STOP_GRACE_SECONDS)]
    assert all(c[2:4] == grace for c in console.calls if c[1] == "stop"), "a quick stop"


@pytest.mark.asyncio
async def test_an_android_that_will_not_stop_does_not_leave_its_gateway_running(
    console: Console,
) -> None:
    console.answer = _docker(fail=lambda argv: argv[1] == "stop" and argv[-1] == "telebuba-acc-0")

    with pytest.raises(EmulatorError):
        await pod.stop_pod("telebuba-acc-0")

    assert _verbs(console) == ["stop telebuba-acc-0", "stop telebuba-acc-0-net"]


@pytest.mark.asyncio
async def test_every_running_pod_is_stopped_androids_before_gateways(console: Console) -> None:
    androids = "0|telebuba-acc-0|running\n1|telebuba-acc-1|running\n2|telebuba-acc-2|exited\n"
    gateways = "0|telebuba-acc-0-net|running\n1|telebuba-acc-1-net|running\n"
    console.answer = _docker(androids, gateways)

    assert await pod.stop_running_pods() == 4

    verbs = _verbs(console)
    assert set(verbs[:2]) == {"stop telebuba-acc-0", "stop telebuba-acc-1"}
    assert set(verbs[2:]) == {"stop telebuba-acc-0-net", "stop telebuba-acc-1-net"}


@pytest.mark.asyncio
async def test_one_pod_that_will_not_stop_does_not_keep_the_others_running(
    console: Console,
) -> None:
    console.answer = _docker(fail=lambda argv: argv[1] == "stop" and argv[-1] == "telebuba-acc-0")

    with pytest.raises(EmulatorError):
        await pod.stop_running_pods()

    assert _verbs(console) == ["stop telebuba-acc-0", "stop telebuba-acc-0-net"]


@pytest.mark.asyncio
async def test_removing_a_pod_forces_both_out_and_is_idempotent(console: Console) -> None:
    console.answer = _docker()

    assert await pod.remove_pod("telebuba-acc-2") is True
    assert await pod.remove_pod("telebuba-gone") is False

    removed = [call[2:] for call in console.calls if call[1] == "rm"]
    assert removed == [["--force", "telebuba-acc-2"], ["--force", "telebuba-acc-2-net"]]


@pytest.mark.asyncio
async def test_a_host_without_docker_has_no_pod_to_remove(console: Console) -> None:
    def missing(_argv: list[str]) -> str:
        code = "emulator_not_found"
        raise EmulatorError(code)

    console.answer = missing

    assert await pod.remove_pod("telebuba-acc-0") is False


@pytest.mark.asyncio
async def test_a_failing_docker_is_not_hidden_from_a_removal(console: Console) -> None:
    console.answer = _docker(fail=lambda _argv: True)

    with pytest.raises(EmulatorError):
        await pod.remove_pod("telebuba-acc-0")


@pytest.mark.asyncio
async def test_an_ipv4_literal_needs_no_lookup() -> None:
    assert await _gateway.resolve_address("127.0.0.1") == "127.0.0.1"


_PAUSED = "0|telebuba-acc-0|paused\n"


@pytest.mark.asyncio
async def test_a_pod_is_parked_by_pausing_android_alone(console: Console) -> None:
    console.answer = _docker()

    assert await pod.pause_pod("telebuba-acc-0") is True
    assert await pod.pause_pod("telebuba-acc-2") is False, "a stopped pod has nothing to pause"

    assert _verbs(console) == ["pause telebuba-acc-0"], "the gateway keeps its tunnel up"


@pytest.mark.asyncio
async def test_a_parked_pod_is_thawed_then_killed_when_stopped(console: Console) -> None:
    console.answer = _docker(_PAUSED)

    await pod.stop_pod("telebuba-acc-0")

    assert _verbs(console) == [
        "unpause telebuba-acc-0",
        "kill telebuba-acc-0",
        "stop telebuba-acc-0-net",
    ], "no grace: the app must not get to reconnect"


@pytest.mark.asyncio
async def test_only_a_pod_still_parked_is_stopped_by_its_timer(console: Console) -> None:
    console.answer = _docker(_PAUSED)
    assert await pod.stop_paused_pod("telebuba-acc-0") is True

    console.calls.clear()
    console.answer = _docker()
    assert await pod.stop_paused_pod("telebuba-acc-0") is False, "someone opened it since"
    assert _verbs(console) == []


@pytest.mark.asyncio
async def test_parked_pods_are_stopped_with_the_running_ones(console: Console) -> None:
    console.answer = _docker(_PAUSED + "1|telebuba-acc-1|running\n")

    assert await pod.stop_running_pods() == 3

    verbs = _verbs(console)
    assert "kill telebuba-acc-0" in verbs
    assert "stop telebuba-acc-1" in verbs


@pytest.mark.asyncio
async def test_a_parked_pod_is_halted_before_a_restart(console: Console) -> None:
    console.answer = _docker(_PAUSED)

    await pod.start_pod("telebuba-acc-0", _SPEC, _PROXY)

    verbs = _verbs(console)
    assert verbs.index("kill telebuba-acc-0") < verbs.index("start telebuba-acc-0-net")


@pytest.mark.asyncio
async def test_one_listing_finds_both_containers_of_a_pod(console: Console) -> None:
    console.answer = _docker()

    await pod.stop_pod("telebuba-acc-0")

    listings = [c for c in console.calls if c[1] == "ps"]
    assert len(listings) == 1
    assert listings[0][4] == "label=telebuba.index"
