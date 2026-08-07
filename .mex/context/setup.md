---
last_updated: 2026-08-06
---

# Setup and Checks

Requires Python 3.13, uv, Node 24/npm and Telegram API credentials.

```bash
uv sync --frozen
cp .env.example .env
uv run pre-commit install --hook-type pre-commit --hook-type pre-push
cd frontend && npm ci && cd ..
uv run uvicorn main:app --reload
```

`.env.example` is the configuration reference. Login needs admin credentials and a 32+ byte `AUTH__SECRET`; provider keys are needed only for enabled features.

## Verify
```bash
uv run ruff check . && uv run ruff format --check . && uv run ty check .
uv run pytest
uv run pre-commit run --all-files
uv run pre-commit run --hook-stage pre-push arch-guard --all-files
uv run pre-commit run --hook-stage pre-push aislop --all-files
uv run python -m tools.gen_api
npm audit --prefix frontend --package-lock-only --audit-level=info
npx --yes mex-agent@0.7.1 check && npx --yes mex-agent@0.7.1 doctor
cd frontend && npm run gates && npm run build
```

Run the relevant subset, not every command blindly. `.github/workflows/*.yml` are the CI source of truth. Code CI and MEX memory CI run on every PR/push to `main`; MEX warnings are blocking. The weekly MEX schedule is a backstop when no PR is open.

The local pre-commit gitleaks hook scans staged changes; CI performs the full-history secret scan. Do not duplicate its container/digest implementation in memory.

On Windows checkouts with `core.autocrlf=true`, repo-wide format hooks may rewrite pre-existing CRLF files. Format touched files and verify real scope with `git diff HEAD --name-only` rather than relying on `git status` alone.
