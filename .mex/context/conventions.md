---
name: conventions
description: Backend coding rules and verification gates.
triggers: [convention, style, naming, rules, review]
edges:
  - target: context/architecture.md
    condition: layer or import question
  - target: context/frontend.md
    condition: code under frontend
  - target: patterns/INDEX.md
    condition: repeatable implementation task
last_updated: 2026-07-16
---

# Conventions

## Hard rules
1. `api/` only validates, calls a service, maps errors, and serializes.
2. Business logic belongs in `services/`; infrastructure and SDK access belong in `core/`.
3. Cross-layer inputs/outputs are Pydantic models from `schemas/`; lists use typed wrappers such as `Page[T]`.
4. DB access uses repositories; Telegram uses typed actions through `core.telegram_client`; providers use their `core/` gateways.
5. No `print()`, raw environment reads, hardcoded operational values, or translated display text in backend responses.
6. Public I/O is async and typed; wrap exceptions with `raise ... from e`.
7. Device fingerprints are immutable after creation.
8. Package roots stay thin; split files by responsibility. Test source files must stay at or below 700 lines.
9. Every behavior change includes tests. Backend branch coverage is at least 90%; warnings, unknown markers, and unexpected xpass fail.
10. Frontend rules live in `context/frontend.md`.

## Placement
- `api/v1/<domain>`: routes and dependencies.
- `services/<domain>`: policies, orchestration, runtime state transitions.
- `core/repositories` and other `core/` modules: persistence and external adapters.
- `schemas/<domain>`: request, response, action, enum, and shared contract models.
- `tests/`: mirror the owning source area where practical.

## Naming
Files/functions/tables use `snake_case`; models use `PascalCase`; functions are verb-first.

## Verify
```bash
uv run ruff check .
uv run ruff format --check .
uv run ty check .
uv run pytest
uv run pre-commit run --all-files
uv run python tools/aislop_gate.py
cd frontend && npm run gates && npm run build
```
Run only the relevant subset during iteration, then the required full gates before merge.