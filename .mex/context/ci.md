---
name: ci
description: Current GitHub Actions merge and nightly gates.
triggers: [ci, workflow, github actions, nightly, merge gate]
edges:
  - target: context/conventions.md
    condition: quality policy
  - target: context/setup.md
    condition: local reproduction
last_updated: 2026-07-16
---

# CI
- `.github/workflows/ci.yml`: pull requests and pushes to `main`.
- `.github/workflows/nightly.yml`: scheduled/manual extended checks.
- CI uses Python 3.13, uv, and Node 24.

## Merge gates
Pre-commit/lint/type/security checks, strict pytest with backend branch coverage ≥90%, dependency audit, Semgrep, aislop, frontend boundaries/lint/format/typecheck/Vitest ≥80%, build, and generated API-client drift.

## Nightly
Extended Hypothesis, full Semgrep, and mutation testing.

## Rules
- Workflow files are the executable source of truth.
- Frontend jobs run for non-documentation code changes; do not assume frontend-only path filtering.
- `.mex/**` and Markdown are ignored by the current CI trigger, so MEX/docs require `npx mex-agent check` and manual review.
- Security dependency overrides must cite the advisory and upstream constraint.
- Local commands are in `context/setup.md`; strictness is defined in `pyproject.toml` and `frontend/package.json`.