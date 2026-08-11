"""Tests for the mutmut 3.6 generated-source integrity guard."""

from __future__ import annotations

from dataclasses import dataclass
from os import utime
from typing import TYPE_CHECKING

import pytest

from tools.mutmut_cli import (
    prepare_pristine_copies_for_generation,
    repair_empty_generated_sources,
    restore_empty_source_copies,
)

if TYPE_CHECKING:
    from pathlib import Path


@dataclass
class _Result:
    error: Exception | None = None


def _source(tmp_path: Path, *, content: str = "value = 1\n") -> Path:
    source = tmp_path / "services" / "sample.py"
    source.parent.mkdir(parents=True)
    source.write_text(content, encoding="utf-8")
    return source.relative_to(tmp_path)


def test_guard_leaves_a_complete_generated_source_untouched(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.chdir(tmp_path)
    source = _source(tmp_path)
    generated = tmp_path / "mutants" / source
    generated.parent.mkdir(parents=True)
    generated.write_text("generated = True\n", encoding="utf-8")

    def unexpected_regeneration(_source: Path) -> _Result:
        pytest.fail("complete generated source must not be regenerated")

    assert repair_empty_generated_sources([source], unexpected_regeneration) == ()
    assert generated.read_text(encoding="utf-8") == "generated = True\n"


def test_guard_allows_an_intentionally_empty_source(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.chdir(tmp_path)
    source = _source(tmp_path, content="")

    def unexpected_regeneration(_source: Path) -> _Result:
        pytest.fail("an empty original source may have an empty generated copy")

    assert repair_empty_generated_sources([source], unexpected_regeneration) == ()


def test_guard_repairs_a_truncated_source_and_discards_stale_metadata(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.chdir(tmp_path)
    source = _source(tmp_path)
    generated = tmp_path / "mutants" / source
    generated.parent.mkdir(parents=True)
    generated.touch()
    metadata = generated.with_suffix(".py.meta")
    metadata.write_text("stale", encoding="utf-8")

    def regenerate(path: Path) -> _Result:
        assert path == source
        assert not metadata.exists()
        generated.write_text("generated = True\n", encoding="utf-8")
        metadata.write_text("fresh", encoding="utf-8")
        return _Result()

    assert repair_empty_generated_sources([source], regenerate) == (source,)
    assert generated.read_text(encoding="utf-8") == "generated = True\n"
    assert metadata.read_text(encoding="utf-8") == "fresh"


def test_guard_fails_if_sequential_repair_is_still_empty(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.chdir(tmp_path)
    source = _source(tmp_path)
    generated = tmp_path / "mutants" / source
    generated.parent.mkdir(parents=True)
    generated.touch()

    with pytest.raises(RuntimeError, match="empty source file after repair"):
        repair_empty_generated_sources([source], lambda _source: _Result())


def test_precoverage_guard_restores_a_truncated_pristine_copy(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.chdir(tmp_path)
    source = _source(tmp_path)
    generated = tmp_path / "mutants" / source
    generated.parent.mkdir(parents=True)
    generated.touch()
    metadata = generated.with_suffix(".py.meta")
    metadata.write_text("stale", encoding="utf-8")

    assert restore_empty_source_copies([source]) == (source,)
    assert generated.read_text(encoding="utf-8") == "value = 1\n"
    assert generated.stat().st_mtime == (tmp_path / source).stat().st_mtime
    assert not metadata.exists()


def test_precoverage_guard_leaves_a_complete_generated_source_untouched(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.chdir(tmp_path)
    source = _source(tmp_path)
    generated = tmp_path / "mutants" / source
    generated.parent.mkdir(parents=True)
    generated.write_text("existing generated tree\n", encoding="utf-8")

    assert restore_empty_source_copies([source]) == ()
    assert generated.read_text(encoding="utf-8") == "existing generated tree\n"


def test_pristine_copy_is_marked_for_real_generation(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.chdir(tmp_path)
    source = _source(tmp_path)
    generated = tmp_path / "mutants" / source
    generated.parent.mkdir(parents=True)
    generated.write_text("value = 1\n", encoding="utf-8")
    source_mtime = (tmp_path / source).stat().st_mtime_ns
    utime(generated, ns=(generated.stat().st_atime_ns, source_mtime + 1_000_000))
    assert generated.stat().st_mtime_ns > (tmp_path / source).stat().st_mtime_ns

    prepare_pristine_copies_for_generation([source])

    assert generated.stat().st_mtime_ns == (tmp_path / source).stat().st_mtime_ns


def test_changed_fresh_copy_fails_before_generation(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.chdir(tmp_path)
    source = _source(tmp_path)
    generated = tmp_path / "mutants" / source
    generated.parent.mkdir(parents=True)
    generated.write_text("changed during coverage\n", encoding="utf-8")

    with pytest.raises(RuntimeError, match="changed during coverage"):
        prepare_pristine_copies_for_generation([source])
