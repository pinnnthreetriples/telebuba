---
last_updated: 2026-08-06
---

# Add Telegram Action

1. Decide write vs read first. Writes join TelegramAction and use execute; reads join TelegramReadAction and use execute_read/execute_read_many.
2. Define typed action/result contracts in the owning schemas/telegram_actions_*.py module and include them in the correct discriminated union. Re-export public classes from schemas.telegram_actions.
3. Implement Telethon dispatch in the focused core/telegram_client/ module and wire its dispatcher. Unsupported actions must fail explicitly; do not leak SDK objects.
4. Let the public gateway own shared Telegram error/rate-limit classification. Domain code consumes stable results rather than remapping raw Telethon exceptions.
5. Persist durable account/domain state in the service layer. Operator profile/media edits that intentionally make FloodWait sticky must be explicitly included in the profile-edit action set; automated warming/neurocomment traffic must not inherit that policy.
6. Call the gateway through the domain seam where tests need substitution.
7. Test success, rate-limit/special classification and generic failure. Patch the symbol on the module that owns it, not a re-exporting package facade.

Pool/session ownership and account-removal rules live in context/runtime-telegram.md.
