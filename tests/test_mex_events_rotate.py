"""``tools/mex_events_rotate.py`` — archiving the mex decision log by calendar year.

The script rewrites decision history, so the tests that matter are the ones about
what it must NOT do: lose a row it cannot parse, re-file rows a previous run already
archived, or move anything out of the year still being written.
"""

from __future__ import annotations

import importlib.util
import json
import sys
from datetime import UTC, datetime
from pathlib import Path
from typing import TYPE_CHECKING

import pytest

if TYPE_CHECKING:
    from types import ModuleType

_SCRIPT = Path(__file__).resolve().parents[1] / "tools" / "mex_events_rotate.py"


def _load() -> ModuleType:
    """Import the script by path — ``tools/`` is deliberately not a package (INP001)."""
    spec = importlib.util.spec_from_file_location("mex_events_rotate", _SCRIPT)
    assert spec is not None
    assert spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _event(stamp: str, message: str) -> str:
    return json.dumps({"timestamp": stamp, "kind": "decision", "message": message})


@pytest.fixture
def rotate(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> ModuleType:
    """The module, pointed at a throwaway `.mex/events` under `tmp_path`."""
    monkeypatch.chdir(tmp_path)
    (tmp_path / ".mex" / "events").mkdir(parents=True)
    module = _load()
    monkeypatch.setattr(sys, "argv", ["mex_events_rotate.py"])
    return module


def _live(tmp_path: Path) -> Path:
    return tmp_path / ".mex" / "events" / "decisions.jsonl"


def _write(tmp_path: Path, lines: list[str]) -> None:
    _live(tmp_path).write_text("\n".join(lines) + "\n", encoding="utf-8")


def test_previous_years_move_and_the_current_one_stays(rotate: ModuleType, tmp_path: Path) -> None:
    """The year still being written is the one an agent reads, so it never moves."""
    this_year = datetime.now(UTC).year
    _write(
        tmp_path,
        [
            _event("2024-05-01T00:00:00Z", "old"),
            _event("2025-06-01T00:00:00Z", "older still"),
            _event(f"{this_year}-01-01T00:00:00Z", "current"),
        ],
    )

    assert rotate.main() == 0

    events = tmp_path / ".mex" / "events"
    assert [
        json.loads(line)["message"]
        for line in _live(tmp_path).read_text(encoding="utf-8").splitlines()
    ] == [
        "current",
    ]
    assert (
        json.loads((events / "decisions-2024.jsonl").read_text(encoding="utf-8"))["message"]
        == "old"
    )
    assert (
        json.loads((events / "decisions-2025.jsonl").read_text(encoding="utf-8"))["message"]
        == "older still"
    )


def test_an_unparseable_row_stays_live_instead_of_being_filed(
    rotate: ModuleType, tmp_path: Path
) -> None:
    """A row we cannot date is a row we cannot file.

    Archiving it by guess would make this script a quiet way to lose the one entry
    somebody has to look at by hand.
    """
    _write(tmp_path, [_event("2024-05-01T00:00:00Z", "old"), "{ not json at all"])

    assert rotate.main() == 0

    assert _live(tmp_path).read_text(encoding="utf-8").strip() == "{ not json at all"


def test_a_second_run_appends_nothing(rotate: ModuleType, tmp_path: Path) -> None:
    """Re-running must not duplicate what the first run already filed."""
    _write(tmp_path, [_event("2024-05-01T00:00:00Z", "old")])
    assert rotate.main() == 0
    archived = (tmp_path / ".mex" / "events" / "decisions-2024.jsonl").read_text(encoding="utf-8")

    assert rotate.main() == 0

    assert (tmp_path / ".mex" / "events" / "decisions-2024.jsonl").read_text(
        encoding="utf-8"
    ) == archived


def test_rotating_twice_in_one_year_keeps_the_earlier_archive(
    rotate: ModuleType, tmp_path: Path
) -> None:
    """Append, never replace — the second batch must not drop the first."""
    _write(tmp_path, [_event("2024-05-01T00:00:00Z", "first")])
    assert rotate.main() == 0
    _write(tmp_path, [_event("2024-09-01T00:00:00Z", "second")])

    assert rotate.main() == 0

    archived = (tmp_path / ".mex" / "events" / "decisions-2024.jsonl").read_text(encoding="utf-8")
    assert [json.loads(line)["message"] for line in archived.splitlines()] == ["first", "second"]


def test_check_reports_without_moving_anything(
    rotate: ModuleType,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """CI's mode: name the overdue years, change nothing, and fail so somebody acts."""
    _write(tmp_path, [_event("2024-05-01T00:00:00Z", "old")])
    monkeypatch.setattr(sys, "argv", ["mex_events_rotate.py", "--check"])

    assert rotate.main() == 1

    assert _live(tmp_path).read_text(encoding="utf-8").strip() != ""
    assert not (tmp_path / ".mex" / "events" / "decisions-2024.jsonl").exists()


def test_check_passes_when_only_the_current_year_is_live(
    rotate: ModuleType,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _write(tmp_path, [_event(f"{datetime.now(UTC).year}-01-01T00:00:00Z", "current")])
    monkeypatch.setattr(sys, "argv", ["mex_events_rotate.py", "--check"])

    assert rotate.main() == 0


def test_a_missing_log_is_not_an_error(rotate: ModuleType) -> None:
    """A repo that has never run `mex log` must not fail the gate."""
    assert rotate.main() == 0
