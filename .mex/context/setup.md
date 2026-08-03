---
last_updated: 2026-08-03
---

# Setup and Checks
Requires Python 3.13, uv, Node 24/npm and Telegram API credentials.

```bash
uv sync --frozen
cp .env.example .env
uv run pre-commit install
cd frontend && npm ci && cd ..
uv run uvicorn main:app --reload
# second terminal: cd frontend && npm run dev
```

`.env.example` is the configuration reference. Login needs admin credentials plus a 32+ byte `AUTH__SECRET`; an empty secret disables token issuance. Gemini/OpenAI keys are needed only by enabled features.

## Verify
```bash
uv run ruff check . && uv run ruff format --check . && uv run ty check .
uv run pytest
uv run pre-commit run --all-files
uv run python tools/aislop_gate.py
uv run python -m tools.gen_api
npm audit --prefix frontend --package-lock-only --audit-level=info  # separate CI job, not in `npm run gates`
npx mex-agent check && npx mex-agent doctor
cd frontend && npm run gates && npm run build   # last: leaves the shell in frontend/
```

CI workflows are the source of truth; nightly adds extended Hypothesis, Semgrep, mutation and a repeat of both CVE audits (the PR-only copies miss anything disclosed during a quiet week). All eight `ci.yml` jobs are **required status checks** on `main`, so they run on every PR including docs-only ones — `paths-ignore` was removed because a workflow that does not run leaves a required check pending forever. `mex.yml` keeps its `paths:` filter and is deliberately NOT required, for the same reason in reverse. `.mex/**` and Markdown do not trigger code CI. Run one uvicorn worker and treat `.session`, tdata, JWT secrets and proxy passwords as credentials.

## Secret scanning
The secret gate is the `lint` job's `gitleaks detect` step, not the pre-commit hook: the hook is `gitleaks protect --staged`, which is right for a local staged diff but scans 0 commits after `actions/checkout` and passes unconditionally, so CI sets `SKIP=gitleaks` on the pre-commit step. The CI step needs `fetch-depth: 0` (`detect` walks history, not a diff), runs the container pinned by digest because it bind-mounts the whole repo plus history, and uses `--verbose --redact` so a finding prints rule/file/line/commit with the value masked. `--exit-code 1` only covers *leaks found* — a git-level failure inside the container exits 0 after scanning nothing — so the step also asserts a non-zero "N commits scanned" in the log, and needs `shell: bash` for pipefail or the `tee` swallows a real leak's exit code. Because the step runs a container, the `lint` job **requires docker on the runner**: fine on `ubuntu-latest`, but a self-hosted runner without it turns a required check red with a bare `docker: command not found`. The step also refuses to run if `.gitleaks.toml` or `.gitleaksignore` exists in the repo: `detect` auto-loads them from the scanned directory and a repo-local config REPLACES the default ruleset rather than extending it, which would scan every commit and find nothing. To reproduce locally: `docker run --rm -v "$PWD:/repo" -w /repo -e GIT_CONFIG_COUNT=1 -e GIT_CONFIG_KEY_0=safe.directory -e GIT_CONFIG_VALUE_0=/repo zricethezav/gitleaks@sha256:0e99e8821643ea5b235718642b93bb32486af9c8162c8b8731f7cbdc951a7f46 detect --redact --verbose --no-banner --exit-code 1` (~55s over 411 commits; the digest, not `:v8.21.2`, for the reason above). Never plant a test secret in this repo to exercise it; use a throwaway repo.
