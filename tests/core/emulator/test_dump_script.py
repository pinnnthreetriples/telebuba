"""The UI dump's script under a real ``sh``, ``uiautomator`` faked as the device's.

uiautomator prints "dumped to" once the file is written and then takes most of a
second to shut its VM down: the script must serve the dump at that print, and must
still end on a failed dump that never prints it.
"""

from __future__ import annotations

import os
import shutil
import subprocess
import time
from typing import TYPE_CHECKING, NamedTuple

import pytest

from core.emulator.adb import _dump_script

if TYPE_CHECKING:
    from pathlib import Path

_SH = shutil.which("sh")
pytestmark = pytest.mark.skipif(_SH is None, reason="needs a POSIX sh")

# A shell function wins over the binary. A good dump writes the file, prints the
# marker and then stays alive for $LINGER seconds (the VM shutdown); a failed one
# prints uiautomator's error and exits 0, as the real tool does; a hung one writes
# the file and never prints.
_FAKE_UIAUTOMATOR = """
uiautomator() {
  if [ "$FAIL" = 1 ]; then echo "ERROR: could not get idle state."; return 0; fi
  echo "<hierarchy rotation=\\"0\\" />" > "$2"
  if [ "$HANG" = 1 ]; then exec sh -c 'echo $$ > "$PIDFILE"; exec sleep "$LINGER"'; fi
  echo "UI hierchary dumped to: $2"
  exec sh -c 'echo $$ > "$PIDFILE"; exec sleep "$LINGER"'
}
"""


class _Run(NamedTuple):
    stdout: str
    seconds: float
    uiautomator_alive: bool


def _script(tmp_path: Path) -> Path:
    xml, log = (tmp_path / "ui.xml").as_posix(), (tmp_path / "ui.log").as_posix()
    script = tmp_path / "dump.sh"
    script.write_text(
        _FAKE_UIAUTOMATOR + " ".join(_dump_script(xml, log)) + "\n", encoding="utf-8", newline="\n"
    )
    return script


def _env(tmp_path: Path, *, fail: bool = False, hang: bool = False, linger: int = 20):
    return {
        "PATH": os.environ["PATH"],
        "FAIL": "1" if fail else "0",
        "HANG": "1" if hang else "0",
        "LINGER": str(linger),
        "PIDFILE": (tmp_path / "uiautomator.pid").as_posix(),
    }


def _run(tmp_path: Path, *, fail: bool = False, linger: int = 20) -> _Run:
    script = _script(tmp_path)
    pidfile = tmp_path / "uiautomator.pid"
    env = _env(tmp_path, fail=fail, linger=linger)
    assert _SH is not None
    started = time.monotonic()
    done = subprocess.run(  # noqa: S603 - test-owned argv
        [_SH, script.as_posix()], env=env, check=True, timeout=30, capture_output=True
    )
    seconds = time.monotonic() - started
    pid = pidfile.read_text(encoding="utf-8").strip() if pidfile.exists() else ""
    alive = bool(pid) and _alive(pid)
    return _Run(done.stdout.decode("utf-8"), seconds, alive)


def _alive(pid: str) -> bool:
    assert _SH is not None
    probe = subprocess.run([_SH, "-c", f"kill -0 {pid}"], check=False, capture_output=True)  # noqa: S603
    return probe.returncode == 0


def test_the_dump_is_served_without_waiting_for_uiautomator_to_exit(tmp_path: Path) -> None:
    run = _run(tmp_path, linger=20)

    assert run.seconds < 10
    assert not run.uiautomator_alive, "the lingering uiautomator was killed at its print"
    marker, _, xml = run.stdout.partition("\n")
    assert "dumped to" in marker
    assert xml.strip() == '<hierarchy rotation="0" />'
    assert not (tmp_path / "ui.log").exists()
    assert not (tmp_path / "ui.xml").exists()


def test_a_failed_dump_ends_the_script_and_serves_no_old_screen(tmp_path: Path) -> None:
    (tmp_path / "ui.xml").write_text("<hierarchy stale />", encoding="utf-8")

    run = _run(tmp_path, fail=True)

    assert run.seconds < 10
    assert "dumped to" not in run.stdout
    assert "stale" not in run.stdout
    assert "could not get idle state" in run.stdout


def test_a_script_cut_off_kills_uiautomator_and_leaves_no_files(tmp_path: Path) -> None:
    script, spid, pidfile = _script(tmp_path), tmp_path / "script.pid", tmp_path / "uiautomator.pid"
    assert _SH is not None
    # As adbd does when adb times out: the script gets a signal mid-poll.
    wrapper = f'sh "{script.as_posix()}" & echo $! > "{spid.as_posix()}"; wait'
    running = subprocess.Popen(  # noqa: S603 - test-owned argv
        [_SH, "-c", wrapper], env=_env(tmp_path, hang=True), stdout=subprocess.DEVNULL
    )
    deadline = time.monotonic() + 10
    while not (pidfile.exists() and spid.exists()) and time.monotonic() < deadline:
        time.sleep(0.05)
    subprocess.run([_SH, "-c", f"kill -TERM {spid.read_text().strip()}"], check=True)  # noqa: S603
    running.wait(timeout=10)

    assert not _alive(pidfile.read_text(encoding="utf-8").strip())
    assert not (tmp_path / "ui.xml").exists()
    assert not (tmp_path / "ui.log").exists()
