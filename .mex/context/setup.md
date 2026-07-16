---
name: setup
description: Supported local-development setup and commands.
triggers: [setup, install, environment, getting started, local development]
edges:
  - target: context/stack.md
    condition: dependency or runtime-version details
  - target: context/ci.md
    condition: reproducing CI locally
last_updated: 2026-07-16
---

# Setup

## Prerequisites

- Python 3.13 and `uv` 0.10+.
- Node.js 24 and npm for the frontend and aislop gate.
- Telegram `api_id`/`api_hash`; optional Gemini/OpenAI keys depending on enabled AI features.
- `ffmpeg` is supplied through `imageio-ffmpeg`; no separate system install is normally required.

## First Run

```bash
uv sync --frozen
cp .env.example .env
uv run pre-commit install
cd frontend && npm ci && cd ..
uv run uvicorn main:app --reload
```

In another terminal:

```bash
cd frontend
npm run dev
```

Backend defaults to `127.0.0.1:8080`; Vite proxies `/api`. SQLite tables and migrations are applied during startup.

## Required Configuration

- `TELEGRAM__API_ID`, `TELEGRAM__API_HASH` for Telegram operations.
- `AUTH__ADMIN_USERNAME`, `AUTH__ADMIN_PASSWORD`, and a 32+ byte `AUTH__SECRET` to enable seeded login. An empty secret disables token issuance.
- `GEMINI__API_KEY` and/or `OPENAI__API_KEY` only for the enabled generation/solver provider.
- Production HTTPS should keep `AUTH__COOKIE_SECURE=true`; local plain HTTP may set it to `false`.

`.env.example` is the complete typed-settings reference and is architecture-tested against config.

## Commands

```bash
uv run pytest
uv run ruff check .
uv run ruff format --check .
uv run ty check .
uv run pre-commit run --all-files
uv run python tools/aislop_gate.py
uv run python -m tools.gen_api
cd frontend && npm run gates && npm run build
```

MEX requires Node 20+; this repository already targets Node 24:

```bash
npx mex-agent check
npx mex-agent doctor
```

## Operational Constraints

- Run one uvicorn worker: runtime tasks and SQLite are process-local.
- Telethon session files are credentials; never commit, print, or copy them into logs.
- Use one coherent environment on Windows; current MEX supports native PowerShell/CMD through `npx mex-agent`.
