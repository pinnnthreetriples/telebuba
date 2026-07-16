---
name: add-telegram-task
description: Add a typed Telegram gateway action.
triggers: [telegram action, telethon, send message, join channel, flood wait]
edges:
  - target: context/telegram.md
    condition: gateway rules
  - target: patterns/add-service.md
    condition: service orchestration
last_updated: 2026-07-16
---

# Add Telegram Action

## Steps
1. Define the action/result contract in `schemas/telegram_actions*.py`.
2. Implement Telethon dispatch in the owning `core/telegram_client/` submodule.
3. Convert SDK values/errors into typed results; never expose Telethon objects.
4. Call the public gateway from a service and persist domain state there.
5. Test success, classified rate limits, and generic failure; patch the owning gateway seam.
6. Run pytest, Ruff, and ty.

## Verify
No Telethon imports or raw client calls outside the gateway; no obsolete `features/` layer; no immediate retries after rate limits; new action is exported and dispatched through the public API.