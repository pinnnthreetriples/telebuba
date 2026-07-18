---
last_updated: 2026-07-18
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
cd frontend && npm run gates && npm run build
npx mex-agent check && npx mex-agent doctor
```

CI workflows are the source of truth; Nightly adds extended Hypothesis,
Semgrep and mutation checks. The mutation job covers `services/` and
`schemas/`, retries the complete sweep once when `mutmut 3.6` leaves incomplete
entries, publishes the
complete results/stats/readable hotspot summary as a 30-day artifact. Individual
survivors do not fail the job; an aggregate score regression, incomplete run or
inconsistent report does. `.mex/**` and Markdown do not trigger code CI. Run one uvicorn worker
and treat `.session`, tdata, JWT secrets and proxy passwords as credentials.
