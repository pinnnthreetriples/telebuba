---
last_updated: 2026-08-04
---

# Add Telegram Action
1. Decide write or read first — it picks everything downstream. Writes join the `TelegramAction` union and run through `execute`, returning `ActionResult`; reads join `TelegramReadAction` and run through `execute_read` / `execute_read_many`, returning their own result model.
2. Define the action/result contracts in the owning `schemas/telegram_actions_*.py` cluster (`_channels`, `_media`, `_privacy`, `_discovery`), import the class into `schemas/telegram_actions.py`, and add it to the right discriminated union there. The union — not the class — is what makes the action dispatchable; the re-import keeps `from schemas.telegram_actions import X` working.
3. Implement Telethon dispatch in the owning `core/telegram_client/` submodule and add a `match` arm in `_actions.py` (writes) or `_read.py` (reads). The write dispatcher has two fall-throughs: an `action_type` starting with `channel_` routes to `_channels._dispatch_channel_action`, and anything unmatched lands in `_dispatch_profile_media_action` — so a missing write arm fails as a confusing media error, not a clean one. `_read.py` has no such fall-through: its `case _` raises `Unsupported read action_type: …`.
4. Convert SDK values/errors to typed results; expose no Telethon objects. `execute` owns the rate-limit ladder (`flood_wait` / `slow_mode_wait` / `premium_wait` / `peer_flood`, plus `already_participant` and `unavailable`); a submodule must let those errors through unmapped.
5. If the action is an operator-driven profile/media edit, add its `action_type` to `_PROFILE_EDIT_ACTION_TYPES` in `_profile.py` so its FloodWait also marks the account. Anything warming or neurocomment drives must stay out of that set: a sticky `flood_wait` status blocks `start_warming` and parks reconcile on restart. Fleet fan-out actions (`set_privacy_settings`) stay out for the same reason.
6. Call the public gateway from a service and persist domain state there.
7. Test success, rate-limit classification and generic failure; patch the seam on the submodule that owns the name (`core.telegram_client._actions.get_client`), never on the `core.telegram_client` namespace.
8. Run pytest, Ruff and ty.

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

Verify: no Telethon/raw client use outside the gateway; no immediate rate-limit retry — cooldowns are persisted and the caller retries later; the action is in its union, has a `match` arm, and (if public) is re-exported from `core/telegram_client/__init__.py`.
