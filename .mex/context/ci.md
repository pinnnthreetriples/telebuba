---
name: ci
description: GitHub Actions CI policy — which checks run on PR vs push to main vs nightly, and what gates a merge. Load when modifying workflows, debugging a red CI, or planning a heavy check.
triggers:
  - "ci"
  - "github actions"
  - "workflow"
  - "pipeline"
  - "nightly"
  - "pr check"
  - "merge gate"
edges:
  - target: context/conventions.md
    condition: when the test/lint policy itself is in question
  - target: context/setup.md
    condition: when the same command runs locally
  - target: state/active.md
    condition: when CI state changes (red main, new known failure)
last_updated: 2026-06-10
---

# CI Policy

Workflows live under `.github/workflows/`. Two files:

- `ci.yml` — fires on `pull_request` and `push` to `main`.
- `nightly.yml` — fires on `cron: "0 3 * * *"` UTC and `workflow_dispatch`.

Dependabot (`.github/dependabot.yml`) opens weekly PRs for GitHub Actions updates.

## What runs when

| Job | PR | Push to main | Nightly | Notes |
|---|---|---|---|---|
| `lint` (pre-commit all hooks) | ✓ | ✓ | — | ruff + ruff-format + bandit + ty + gitleaks + hygiene |
| `test` (pytest strict) | ✓ | ✓ | extended profile only | Hypothesis: `dev` (50) on PR, `strict` (200) on main, `extended` (2000) nightly |
| `audit` (pip-audit) | ✓ | ✓ | — | CVEs in deps |
| `semgrep` (auto config) | — | ✓ | — | gated to main only — too slow for PR |
| `semgrep-full` (3 rulesets) | — | — | ✓ | security-audit + OWASP top 10 + python |
| `extended-hypothesis` | — | — | ✓ | 2000 examples per property |

## Triggers and skips

- **`paths-ignore`** on both PR and push: `.mex/**`, `**.md`, `.gitignore`, `LICENSE`. Doc-only commits do not start CI.
- **`workflow_dispatch`** on both `ci` and `nightly` — re-runnable from the GitHub UI.
- **`concurrency` cancel-in-progress** — a new push cancels the previous run on the same branch.

## Strictness inheritance

CI does not redefine strictness; it inherits from `pyproject.toml [tool.pytest.ini_options]`. Whatever fails locally fails in CI. Conversely, if CI passes and local fails, the diff is in environment, not config.

`HYPOTHESIS_PROFILE` is set per job:
- PR job → `dev` (50 examples, fast feedback)
- main push → `strict` (200 examples)
- nightly → `extended` (2000 examples)

Selection lives in `conftest.py`.

## Caches

- `setup-uv@v6` with `enable-cache: true` — uv caches `.venv` and the package cache per workflow.
- `actions/cache@v4` keyed on `hashFiles('.pre-commit-config.yaml')` — pre-commit hook envs persist between runs (saves 30–40 s per lint job).

## Override / override-dependencies policy

`pyproject.toml [tool.uv].override-dependencies` is the only place we pin past upstream constraints (e.g. CVE-driven bumps). Every entry MUST carry a comment naming the advisory IDs and the upstream chain that pins the bad version. Reviewed on every `semgrep` / `pip-audit` failure.

## What does NOT belong in CI

- `pytest-xdist` parallelism — add only when test count justifies it; with the canary alone it is noise.
- `mutmut` — gated to nightly + `workflow_dispatch` once it has real code to mutate. See `state/active.md → Open Decisions`.
- Branch protection / required-status-check setup — that lives in repo settings, not workflows.

## Known gotcha

The pytest job is **red until the first real feature ships** because `--cov-fail-under=90` over zero source files yields 0 % coverage. Accepted by policy: green CI on no code would be a lie. Re-greens automatically with the first feature + tests.
