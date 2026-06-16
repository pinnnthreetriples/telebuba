---
name: add-feature
description: Add a new user-facing feature as a UI-thin module/package under features/, with schemas, services, and tests.
triggers:
  - "add feature"
  - "new feature"
  - "new page"
  - "features/"
edges:
  - target: context/conventions.md
    condition: always — feature domains have hard structural rules
  - target: context/architecture.md
    condition: when deciding what belongs in core/ vs services/ vs the feature
  - target: patterns/add-telegram-task.md
    condition: when the feature performs Telegram I/O
  - target: patterns/add-warming-job.md
    condition: when the feature touches warming runtime work
last_updated: 2026-06-16
---

# Add a Feature

## Context

Read `context/conventions.md` and `context/services.md` first. Key constraints:
- A feature is UI + service/domain logic. UI lives in `features/`; business logic lives in `services/`.
- Small feature: `features/<name>.py`. Larger feature: `features/<name>/` package with a thin `__init__.py`.
- No imports from another feature domain.
- All function inputs/outputs crossing layers are Pydantic models in `schemas/`.

## Steps

1. **Schema first.** Add or extend `schemas/<domain>.py`. Define request/response models the feature and service will use at boundaries. Extend `schemas/telegram_actions.py` only if a new Telegram action is needed.
2. **Service first.** Add or extend `services/<domain>.py` or `services/<domain>/` with business logic. Public functions take/return Pydantic models and delegate I/O to `core/*`.
3. **Test the service.** Add/update tests under `tests/services/`. Mock `core/*` adapters. Prefer `/tdd` skill.
4. **Create/update the feature UI.** Add `features/<name>.py` or `features/<name>/`. Keep handlers thin: validate → call service → render.
5. **Wire it in.** Register the page/route once from `main.py` or the existing feature registration point.
6. **Test the feature.** Add/update tests under `tests/features/` when logic is testable outside direct NiceGUI rendering. Mock the service; do not re-test service logic here.
7. **Run gates.** `uv run pytest`, `uv run ruff check .`, `uv run ty check`, and relevant pre-commit hooks.

## Gotchas

- It is tempting to `from features.accounts import ...` when two features need the same helper. Don't — move shared behavior to `services/`, `core/`, or `schemas/`.
- NiceGUI handlers run on the same loop as runtime tasks. Anything CPU-bound or sync-blocking will freeze the UI. Use `await` or offload.
- Pydantic v2 model construction is strict by default — build models explicitly at boundaries.
- If a feature package root grows large, split render helpers into `_table.py`, `_dialogs.py`, `_board.py`, `_config.py`, etc.

## Verify

- [ ] Feature is isolated under `features/<name>.py` or `features/<name>/`
- [ ] No imports from another feature domain
- [ ] Handlers are thin: validate → call service → render
- [ ] No `sqlalchemy` / `telethon` import in the feature or service
- [ ] All cross-layer function signatures use Pydantic models from `schemas/`
- [ ] No `print`, no inline secrets, no literal tunables; config via `settings.<namespace>.<field>`
- [ ] Service tests exist or were updated; feature tests exist where practical
- [ ] `uv run pytest` passes

## Debug

- Import error referencing another feature → you crossed the no-cross-feature-import rule; move shared code to `services/`, `core/`, or `schemas/`.
- `ValidationError` at a handler edge → the caller passed a wrong-shaped model; fix the boundary, not the schema strictness.
- UI freezes when the handler runs → a sync call is blocking the loop; wrap with `asyncio.to_thread` or await the async variant.

## After

Run the GROW step from `ROUTER.md` (update `state/active.md`, touch any out-of-date `context/`, bump `last_updated`).
