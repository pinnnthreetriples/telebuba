"""Root pytest configuration.

Test policy: see ``.mex/context/conventions.md``.
All knobs that affect pass/fail live in ``pyproject.toml [tool.pytest.ini_options]``.
This file is for fixtures and Hypothesis profile registration only.

Profile selection: set ``HYPOTHESIS_PROFILE`` to ``dev`` | ``strict`` |
``extended`` | ``mutation``. Defaults to ``strict`` (CI on main, local runs).
CI on PR sets ``dev``; nightly uses ``extended`` for the property suite and the
deterministic ``mutation`` profile for mutmut.
"""

from __future__ import annotations

import os

from hypothesis import HealthCheck, Verbosity, settings

# Strict profile — default. Used locally and on push to main.
settings.register_profile(
    "strict",
    database=None,
    deadline=None,
    derandomize=True,
    max_examples=200,
    print_blob=True,
    report_multiple_bugs=True,
    suppress_health_check=[HealthCheck.too_slow],
    verbosity=Verbosity.normal,
)

# Dev profile — fast inner loop and PR runs.
settings.register_profile(
    "dev",
    parent=settings.get_profile("strict"),
    max_examples=50,
)

# Extended profile — nightly. Higher chance of surfacing rare bugs.
settings.register_profile(
    "extended",
    parent=settings.get_profile("strict"),
    max_examples=2000,
)

# Mutation profile — stable examples are part of the measurement contract.
# ``mutate_only_covered_lines`` turns Hypothesis coverage into the mutant
# catalogue, so a random example set would make both the denominator and score
# drift between otherwise identical Nightly runs.
settings.register_profile(
    "mutation",
    parent=settings.get_profile("strict"),
    max_examples=200,
)

_profile = os.environ.get("HYPOTHESIS_PROFILE", "strict")
if _profile not in {"strict", "dev", "extended", "mutation"}:
    msg = f"Unknown HYPOTHESIS_PROFILE: {_profile!r} (expected: strict | dev | extended | mutation)"
    raise RuntimeError(msg)
settings.load_profile(_profile)
