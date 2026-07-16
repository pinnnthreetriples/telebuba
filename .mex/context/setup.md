---
name: setup
description: Local setup and canonical commands.
triggers: [setup, install, environment, local development]
edges:
  - target: context/stack.md
    condition: dependencies
  - target: context/ci.md
    condition: reproduce gates
last_updated: 2026-07-16
---

# Setup
Requires Python 3.13, uv, Node 24/npm, and Telegram API credentials.

```bash
uv sync --frozen
cp .env.example .env
uv run pre-commit install
cd frontend && npm ci && cd ..
uv run uvicorn main:app --reload
# second terminal
cd frontend && npm run dev
```

`.env.example` is the configuration reference. Telegram operations require `TELEGRAM__API_ID` and `TELEGRAM__API_HASH`. Login needs admin credentials and a 32+ byte `AUTH__SECRET`; an empty secret disables token issuance. Gemini/OpenAI keys are required only by enabled features.

## Gates
```bash
uv run ruff check .
uv run ruff format --check .
uv run ty check .
uv run pytest
uv run pre-commit run --all-files
uv run python tools/aislop_gate.py
uv run python -m tools.gen_api
cd frontend && npm run gates && npm run build
npx mex-agent check
npx mex-agent doctor
```

Run one uvicorn worker. Treat `.session`, tdata, JWT secrets, and proxy passwords as credentials.