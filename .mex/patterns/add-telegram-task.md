---
name: add-telegram-task
description: Add code that performs a Telegram action (send message, join chat, comment, etc.) via Telethon, safely scoped to one account.
triggers:
  - "telegram action"
  - "telethon"
  - "send message"
  - "join channel"
  - "comment"
  - "FloodWait"
edges:
  - target: context/telegram.md
    condition: always — defines the rules for Telethon usage
  - target: patterns/add-feature.md
    condition: when the Telegram action is part of a new feature
  - target: patterns/add-warming-job.md
    condition: when the action runs on a schedule rather than user click
last_updated: 2026-06-10
---

# Add a Telegram Task

## Context

Read `context/telegram.md`. Hard rules:
- All Telethon clients come from `core/telegram_client.py`. Do not construct `TelegramClient` directly in a feature.
- One session file per account, opened by at most one client at a time.
- Catch `FloodWaitError` at the feature boundary.

## Steps

1. **Define schemas.** A request model carrying the account id and action params; a result model carrying outcome + any return data. Both in `schemas/`.
2. **Get a client.** Call the factory in `core/telegram_client.py` with the account id. [VERIFY AFTER FIRST IMPLEMENTATION — exact factory signature once `core/telegram_client.py` exists.]
3. **Perform the action.** Inside the client context, call the Telethon method you need. Convert the Telethon return value into your Pydantic result model before leaving the function.
4. **Handle `FloodWaitError`.** Catch it, log a business event via `core/logging.py` with the account id and wait seconds, and either return a failure result or reschedule via APScheduler.
5. **Persist the result.** If the action changed account state (e.g. status, last action time), update via `core/db.py` using a schema, not a raw dict.
6. **Test.** Mock the Telethon client in `tests/`; cover happy path, `FloodWaitError`, and one generic Telegram error.

## Gotchas

- Opening the same session file twice corrupts it. Never bypass the factory.
- `FloodWaitError.seconds` can be hours — do not `await asyncio.sleep(e.seconds)` inline; reschedule.
- Telethon returns rich objects (`Message`, `User`, etc.) — never let them leak into NiceGUI or schemas; map to your own Pydantic model.
- Some Telethon calls are sync-looking but are coroutines; missing `await` silently does nothing useful.

## Verify

- [ ] No direct `TelegramClient(...)` construction outside `core/telegram_client.py`
- [ ] `FloodWaitError` is caught and produces either a logged failure result or a reschedule
- [ ] No Telethon types in function signatures crossing into features/schemas/UI
- [ ] Test mocks the Telethon client and covers the FloodWait path
- [ ] `uv run pytest` passes

## Debug

- "database is locked" alongside Telegram errors → Telethon's session SQLite is contending with our app's SQLite or with another client; check the factory isn't double-issuing.
- Action silently does nothing → missing `await` on the Telethon coroutine.
- Repeated FloodWaits on the same account → backoff/reschedule logic is firing the call too soon; widen the next-run delay.

## After
Run the GROW step from `ROUTER.md` (update `state/active.md`, touch any out-of-date `context/`, bump `last_updated`).
