---
last_updated: 2026-07-26
---

# Add Telegram Action
1. Define typed action/result contracts in `schemas/telegram_actions*.py`.
2. Implement Telethon dispatch in the owning `core/telegram_client/` submodule.
3. Convert SDK values/errors to typed results; expose no Telethon objects.
4. Call the public gateway from a service and persist domain state there.
5. Test success, rate-limit classification and generic failure; patch the owning seam.
6. Run pytest, Ruff and ty.

Acting on a peer the session may never have met (a DM partner, a fresh channel):
resolve it, do not pass a raw id — a cold session has no cached `access_hash` and
Telethon raises a bare `ValueError` (see `_dm._resolve_dm_peer`). Prefer a lookup
with no write side effect; anything that saves a contact or joins leaves a
cross-account trace that outlives the action.

Naming a permanent failure as its own exception class is what lets a service skip
instead of fail — but scope the `except` to errors that are genuinely permanent.
Telethon collapses exhausted retries into a bare `ValueError`, so a broad catch
turns a passing outage into "give up on this peer forever". Test both sides of
that line; a test that only proves the permanent case will pass with the
distinction deleted.

Verify: no Telethon/raw client use outside the gateway; no immediate rate-limit retry; the action is exported and publicly dispatched.
