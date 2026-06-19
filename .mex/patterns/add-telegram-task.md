---
name: add-telegram-task
description: Add code that performs a Telegram action through the typed core.telegram_client executor.
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
    condition: when the action runs inside the warming runtime
last_updated: 2026-06-19
---

# Add a Telegram Action

## Context

Read `context/telegram.md`. Hard rules:
- All Telethon code lives inside `core/telegram_client/`.
- App code imports only the public API from `core.telegram_client`.
- Services/features do not construct `TelegramClient` and do not call raw client methods.
- New actions are Pydantic models in `schemas/telegram_actions.py` and are dispatched by the core executor.

## Steps

1. **Define the action schema.** Add a Pydantic action model in `schemas/telegram_actions.py` and include it in the `TelegramAction` union/discriminator.
2. **Implement dispatch in the gateway.** Add the Telethon-specific call inside `core/telegram_client/_actions.py` or a focused helper submodule owned by the gateway.
3. **Return typed results.** Convert SDK outcomes/errors into `ActionResult`. Do not leak Telethon objects upward.
4. **Use from services only.** Service code builds the typed action and calls `await execute(account_id, action)`.
5. **Persist domain state above the gateway.** If the action changes domain state, persist it from the service/runtime layer, not inside a Telethon callback.
6. **Test.** Mock the gateway/client. Cover success, rate-limit/failure classification, and generic SDK failure.
7. **Run gates.** `uv run pytest`, `uv run ruff check .`, `uv run ty check`.

## Gotchas

- Do not bypass the gateway because a raw SDK call looks shorter.
- Do not import Telethon in `services/` or `features/`.
- Do not let rich SDK return objects cross into schemas/UI.
- Do not retry rate-limited actions immediately from a service; return/persist the typed status and let the owning domain decide next state.
- Tests should patch the owning gateway submodule, not random package re-exports.

## Verify

- [ ] No direct `TelegramClient(...)` construction outside `core/telegram_client/`
- [ ] New action is a Pydantic schema
- [ ] `core.telegram_client.execute(account_id, action)` handles the action
- [ ] No Telethon types in function signatures crossing into services/features/schemas
- [ ] Tests cover success and failure/rate-limit classification
- [ ] `uv run pytest` passes

## Debug

- Action silently does nothing → check missing `await` inside the gateway dispatch.
- Type checker does not narrow the action → check the union/discriminator in `schemas/telegram_actions.py`.
- Service test has to mock Telethon directly → the gateway boundary is leaking; mock `execute` or the gateway-owned submodule instead.

## After

Run the GROW step from `ROUTER.md` (update `state/active.md`, touch any out-of-date `context/`, bump `last_updated`).
