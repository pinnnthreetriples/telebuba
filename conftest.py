"""Root pytest configuration.

Test policy: see `.mex/context/conventions.md` rule 7 (Test Coverage).
All knobs that affect pass/fail live in `pyproject.toml [tool.pytest.ini_options]`.
This file is for fixtures and Hypothesis profile registration only.

Profile selection: set `HYPOTHESIS_PROFILE` to `dev` | `strict` | `extended`.
Defaults to `strict` (CI on main, local runs). CI on PR sets `dev`; nightly
sets `extended`.
"""

from __future__ import annotations

import os

from hypothesis import HealthCheck, Verbosity, settings

# Strict profile — default. Used locally and on push to main.
settings.register_profile(
    "strict",
    deadline=None,
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

_profile = os.environ.get("HYPOTHESIS_PROFILE", "strict")
if _profile not in {"strict", "dev", "extended"}:
    msg = f"Unknown HYPOTHESIS_PROFILE: {_profile!r} (expected: strict | dev | extended)"
    raise RuntimeError(msg)
settings.load_profile(_profile)
