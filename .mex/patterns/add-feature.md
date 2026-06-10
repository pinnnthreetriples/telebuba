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

Read `context/conventions.md` for the structural rules. Key constraints:
- One new file in `features/<name>.py`. Do not edit existing feature files.
- No imports from other `features/*.py`. Shared logic goes in `core/` or `schemas/`.
- All function inputs/outputs are Pydantic models in `schemas/`.

## Steps

1. **Schema first.** Add `schemas/<name>.py` (or extend an existing schema file only if it is conceptually the same domain). Define the request/response models the feature will use at its boundaries.
2. **Create the feature file.** `features/<name>.py` exposes a NiceGUI page or component and the async handlers that drive it. Handlers accept and return your Pydantic models.
3. **Use core helpers.** Any DB access → `core/db.py`. Any Telegram I/O → `core/telegram_client.py`. Any config → `core/config.py`. Any logging → `core/logging.py`.
4. **Wire it in.** Register the page/route once at app startup. [VERIFY AFTER FIRST IMPLEMENTATION — exact registration site once `main.py` exists.]
5. **Add the test — prefer `/tdd`.** Use the `tdd` skill to drive the implementation red-green-refactor when the behaviour can be expressed as a test first. Cover at least: happy path, one validation failure, one core-helper failure. The test lives in `tests/features/test_<name>.py`.
6. **Run `uv run pytest`.** Must pass before the feature is considered done.

## Gotchas

- It is tempting to `from features.accounts import ...` when two features need the same helper. Don't — promote the helper to `core/` instead.
- NiceGUI handlers run on the same loop as the scheduler. Anything CPU-bound or sync-blocking will freeze the UI. Use `await` or offload.
- Pydantic v2 model construction is strict by default — don't rely on coercion from dicts coming out of SQLAlchemy; build the model explicitly.

## Verify

- [ ] New file under `features/`, not an edit of an existing one
- [ ] No `from features.<other>` imports
- [ ] All handler signatures use Pydantic models from `schemas/`
- [ ] No `print`, no inline secrets, no literal tunables
- [ ] `tests/features/test_<name>.py` exists and `uv run pytest` passes

## Debug

- Import error referencing another feature → you crossed the no-cross-feature-import rule; move the shared code to `core/`.
- `ValidationError` at a handler edge → the caller passed a dict or wrong-shaped model; fix at the call site, not by loosening the schema.
- UI freezes when the handler runs → a sync call is blocking the loop; wrap with `asyncio.to_thread` or await the async variant.

## After
Run the GROW step from `ROUTER.md` (update `state/active.md`, touch any out-of-date `context/`, bump `last_updated`).
