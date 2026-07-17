---
last_updated: 2026-07-16
---

# Add Telegram Action
1. Define typed action/result contracts in `schemas/telegram_actions*.py`.
2. Implement Telethon dispatch in the owning `core/telegram_client/` submodule.
3. Convert SDK values/errors to typed results; expose no Telethon objects.
4. Call the public gateway from a service and persist domain state there.
5. Test success, rate-limit classification and generic failure; patch the owning seam.
6. Run pytest, Ruff and ty.

Verify: no Telethon/raw client use outside the gateway; no immediate rate-limit retry; the action is exported and publicly dispatched.
