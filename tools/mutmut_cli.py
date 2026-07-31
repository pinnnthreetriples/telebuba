"""Run mutmut 3.6 with Loguru loaded before its module snapshot.

mutmut gathers coverage, unloads modules imported after its initial snapshot,
then runs the baseline suite again. If Loguru is first imported by the tests,
an ``enqueue=True`` handler can retain the old ``Message`` class while the
module is re-imported. Multiprocessing then rejects records from the new class
with a ``PicklingError``. Preloading Loguru keeps one class identity across both
runs without changing production logging or excluding any test.
"""

from __future__ import annotations

from importlib import import_module
from os import utime
from pathlib import Path
from shutil import copy2
from sys import stderr
from typing import TYPE_CHECKING, Protocol, cast

if TYPE_CHECKING:
    from collections.abc import Callable, Iterable, Iterator


class _FileMutationResult(Protocol):
    error: Exception | None


class _MutmutMain(Protocol):
    create_mutants: Callable[[int], object]
    create_file_mutants: Callable[[Path], _FileMutationResult]
    cli: Callable[[], None]
    store_lines_covered_by_tests: Callable[[], None]

    def walk_mutatable_files(self) -> Iterator[Path]: ...


def restore_empty_source_copies(source_paths: Iterable[Path]) -> tuple[Path, ...]:
    """Restore pristine copies before coverage is measured in a reused tree."""
    restored: list[Path] = []
    for source_path in source_paths:
        generated_path = Path("mutants") / source_path
        if source_path.stat().st_size == 0:
            continue
        if generated_path.is_file() and generated_path.stat().st_size > 0:
            continue

        generated_path.parent.mkdir(parents=True, exist_ok=True)
        copy2(source_path, generated_path)
        generated_path.with_suffix(f"{generated_path.suffix}.meta").unlink(missing_ok=True)
        restored.append(source_path)
    return tuple(restored)


def prepare_pristine_copies_for_generation(source_paths: Iterable[Path]) -> None:
    """Validate fresh copies and defeat mutmut's strict mtime cache check."""
    for source_path in source_paths:
        generated_path = Path("mutants") / source_path
        metadata_path = generated_path.with_suffix(f"{generated_path.suffix}.meta")
        if metadata_path.exists():
            continue
        if source_path.read_bytes() != generated_path.read_bytes():
            msg = f"fresh mutmut source copy changed during coverage: {generated_path}"
            raise RuntimeError(msg)
        source_stat = source_path.stat()
        generated_stat = generated_path.stat()
        utime(generated_path, ns=(generated_stat.st_atime_ns, source_stat.st_mtime_ns))


def repair_empty_generated_sources(
    source_paths: Iterable[Path],
    regenerate: Callable[[Path], _FileMutationResult],
) -> tuple[Path, ...]:
    """Sequentially repair truncated generated files before mutmut runs stats."""
    repaired: list[Path] = []
    for source_path in source_paths:
        generated_path = Path("mutants") / source_path
        if source_path.stat().st_size == 0:
            continue
        if generated_path.is_file() and generated_path.stat().st_size > 0:
            continue

        generated_path.unlink(missing_ok=True)
        generated_path.with_suffix(f"{generated_path.suffix}.meta").unlink(missing_ok=True)
        result = regenerate(source_path)
        if result.error is not None:
            raise result.error
        if not generated_path.is_file() or generated_path.stat().st_size == 0:
            msg = f"mutmut generated an empty source file after repair: {generated_path}"
            raise RuntimeError(msg)
        repaired.append(source_path)
    return tuple(repaired)


def _install_generated_source_guard(mutmut_main: _MutmutMain) -> None:
    upstream_store_coverage = mutmut_main.store_lines_covered_by_tests
    upstream_create_mutants = mutmut_main.create_mutants

    def store_coverage_with_validation() -> None:
        restored = restore_empty_source_copies(mutmut_main.walk_mutatable_files())
        if restored:
            rendered = ", ".join(str(path) for path in restored)
            stderr.write(f"Restored truncated mutmut source copies before coverage: {rendered}\n")
        upstream_store_coverage()
        prepare_pristine_copies_for_generation(mutmut_main.walk_mutatable_files())

    def create_mutants_with_validation(max_children: int) -> object:
        stats = upstream_create_mutants(max_children)
        repaired = repair_empty_generated_sources(
            mutmut_main.walk_mutatable_files(),
            mutmut_main.create_file_mutants,
        )
        if repaired:
            rendered = ", ".join(str(path) for path in repaired)
            stderr.write(f"Sequentially repaired truncated mutmut sources: {rendered}\n")
        return stats

    mutmut_main.store_lines_covered_by_tests = store_coverage_with_validation
    mutmut_main.create_mutants = create_mutants_with_validation


def main() -> None:
    """Preload Loguru, then delegate to mutmut's pinned Click entry point."""
    import_module("loguru")
    mutmut_main = cast("_MutmutMain", import_module("mutmut.__main__"))
    _install_generated_source_guard(mutmut_main)
    mutmut_main.cli()


if __name__ == "__main__":
    main()
