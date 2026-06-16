"""Cyclomatic-complexity gate: fail when any block ranks D or worse (cc > 20).

radon never exits non-zero on its own, so this walks the source packages with
radon's API and fails the build if any function/method exceeds cyclomatic
complexity 20. The project chose the D+ threshold (cc <= 20 allowed) over the
stricter C+ to catch genuinely-overgrown functions without forcing artificial
splits of legitimately branchy domain logic.
"""

from __future__ import annotations

import sys
from pathlib import Path
from typing import TYPE_CHECKING

from radon.complexity import cc_visit

if TYPE_CHECKING:
    from collections.abc import Iterator

_MAX_COMPLEXITY = 20
_PATHS = ("core", "features", "schemas", "services", "main.py")


def _iter_python_files(paths: tuple[str, ...]) -> Iterator[Path]:
    for raw in paths:
        path = Path(raw)
        if path.is_file():
            yield path
        elif path.is_dir():
            yield from sorted(path.rglob("*.py"))


def main() -> int:
    failures = [
        f"{file}:{block.lineno} {block.name} (cc={block.complexity})"
        for file in _iter_python_files(_PATHS)
        for block in cc_visit(file.read_text(encoding="utf-8"))
        if block.complexity > _MAX_COMPLEXITY
    ]
    if failures:
        sys.stdout.write("radon: blocks exceeding cyclomatic complexity 20 (rank D+):\n")
        for line in failures:
            sys.stdout.write(f"  {line}\n")
        return 1
    sys.stdout.write("radon: complexity gate passed (every block <= cc 20)\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
