---
last_updated: 2026-07-25
---

# Setup and Checks
Requires Python 3.13.14, uv, Node 24/npm and Telegram API credentials.

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

CI workflows are the source of truth; Nightly adds extended Hypothesis,
Semgrep, mutation and a repeat of both CVE audits (the PR/push copies miss
anything disclosed during a quiet week). All eight `ci.yml` jobs are **required
status checks** on `main`, so they run on every PR including docs-only ones;
`paths-ignore` must not be restored without same-named no-op jobs. `mex.yml`
keeps its `paths:` filter and is deliberately not required.

The mutation job covers `services/` and `schemas/` under the deterministic
`mutation` profile. With the supported `mutmut 3.6` commands, Nightly preserves
the first raw snapshot and repairs only incomplete identities in a separate
targeted snapshot. The 30-day artifact includes both raw attempts, official
first-attempt stats, measurement metadata and the readable project report.
Individual survivors do not fail the job; an aggregate score regression,
catalogue/config drift, unreviewed timeout, incomplete repair, collection error
or inconsistent report does. Run one uvicorn worker and treat `.session`, tdata,
JWT secrets and proxy passwords as credentials.
