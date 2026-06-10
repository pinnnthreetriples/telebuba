"""Root pytest configuration.

Test policy: see `.mex/context/conventions.md` rule 7 (Test Coverage).
All knobs that affect pass/fail live in `pyproject.toml [tool.pytest.ini_options]`.
This file is for fixtures and Hypothesis profile registration only.
"""

from __future__ import annotations

from hypothesis import HealthCheck, Verbosity, settings

# Strict Hypothesis profile — used in CI and by default locally.
# - deadline=None: async tests on Windows are jittery; we keep accuracy over speed
# - max_examples=200: more than the default 100, still fast for unit tests
# - derandomize=False: keep randomness; reproducibility comes from the seed printed on failure
# - print_blob=True: full reproducer blob on failure
# - report_multiple_bugs=True: surface every bug per run, not just the first
settings.register_profile(
    "strict",
    deadline=None,
    max_examples=200,
    print_blob=True,
    report_multiple_bugs=True,
    suppress_health_check=[HealthCheck.too_slow],
    verbosity=Verbosity.normal,
)

# Dev profile — fewer examples for fast inner loop.
settings.register_profile("dev", parent=settings.get_profile("strict"), max_examples=50)

settings.load_profile("strict")
