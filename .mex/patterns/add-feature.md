---
name: add-feature
description: Add a new user-facing feature as its own file in features/, with schemas and a pytest test.
triggers:
  - "add feature"
  - "new feature"
  - "new page"
  - "features/"
edges:
  - target: context/conventions.md
    condition: always — feature files have hard structural rules
  - target: context/architecture.md
    condition: when deciding what belongs in core/ vs the feature
  - target: patterns/add-telegram-task.md
    condition: when the feature performs Telegram I/O
  - target: patterns/add-warming-job.md
    condition: when the feature registers a scheduled job
last_updated: 2026-06-10
---

# Add a Feature

## Context

Read `context/conventions.md` and `context/services.md` first. Key constraints:
- A "feature" = UI + a service. Both files are NEW. Existing feature/service files are never edited.
- `features/<name>.py` is **UI-thin** (NiceGUI page + 3-line handlers). All business logic lives in `services/<domain>.py`.
- No imports from other `features/*.py`.
- All function inputs/outputs are Pydantic models in `schemas/`.

## Steps

1. **Schema first.** Add `schemas/<domain>.py` (or extend an existing one only if the same domain). Define request/response models the feature and service will use at their boundaries. Also extend `schemas/telegram_actions.py` if a new Telegram action is needed (see `patterns/add-telegram-task.md`).
2. **Service first.** Add `services/<domain>.py` with the business logic. Pure async functions taking and returning Pydantic models. Delegates I/O to `core/*` (DB, telegram executor, http, logging). See `patterns/add-service.md` for the service-specific protocol.
3. **Test the service.** `tests/services/test_<domain>.py` — happy path + at least one failure path. Mock `core/*` adapters. Prefer `/tdd` skill.
4. **Create the feature file.** `features/<name>.py` exposes a NiceGUI page or component and 3-line async handlers: validate → call service → render. Handlers accept and return Pydantic models. **No business logic here.**
5. **Wire it in.** Register the page/route once at app startup. [VERIFY AFTER FIRST IMPLEMENTATION — exact registration site once `main.py` exists.]
6. **Test the feature.** `tests/features/test_<name>.py` — mock the service; verify the handler validates, delegates, and renders correctly. Do NOT re-test the service logic here.
7. **Run `uv run pytest`.** Strict mode must pass (warnings → errors, coverage ≥ 90 %).

## Gotchas

- It is tempting to `from features.accounts import ...` when two features need the same helper. Don't — promote the helper to `core/` instead.
- NiceGUI handlers run on the same loop as the scheduler. Anything CPU-bound or sync-blocking will freeze the UI. Use `await` or offload.
- Pydantic v2 model construction is strict by default — don't rely on coercion from dicts coming out of SQLAlchemy; build the model explicitly.

## Verify

- [ ] New files under `features/` AND `services/`, not edits of existing ones
- [ ] No `from features.<other>` imports
- [ ] `features/<name>.py` handler bodies are ≤ 5 lines each (validate → call service → render)
- [ ] No `sqlalchemy` / `telethon` / `nicegui` import in the service
- [ ] All function signatures use Pydantic models from `schemas/`
- [ ] No `print`, no inline secrets, no literal tunables; config via `settings.<namespace>.<field>`
- [ ] `tests/services/test_<domain>.py` AND `tests/features/test_<name>.py` exist; `uv run pytest` passes

## Debug

- Import error referencing another feature → you crossed the no-cross-feature-import rule; move the shared code to `core/`.
- `ValidationError` at a handler edge → the caller passed a dict or wrong-shaped model; fix at the call site, not by loosening the schema.
- UI freezes when the handler runs → a sync call is blocking the loop; wrap with `asyncio.to_thread` or await the async variant.

## After
Run the GROW step from `ROUTER.md` (update `state/active.md`, touch any out-of-date `context/`, bump `last_updated`).
