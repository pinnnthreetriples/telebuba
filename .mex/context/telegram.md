---
name: telegram
description: Telethon integration — session handling, client lifecycle, rate limits, and the rules around touching Telegram from this codebase.
triggers:
  - "telegram"
  - "telethon"
  - "session"
  - "MTProto"
  - "account"
  - "flood wait"
edges:
  - target: context/architecture.md
    condition: when placing Telegram code in the right layer
  - target: context/warming.md
    condition: when the Telegram call is being scheduled, not direct
  - target: patterns/add-telegram-task.md
    condition: when adding any new Telethon-using feature
last_updated: 2026-06-10
---

# Telegram (Telethon) Integration

## Where Telegram code lives

- All Telethon client construction and lifecycle is in `core/telegram_client.py`. Features must not instantiate `TelegramClient` directly.
- Feature files in `features/` ask `core/telegram_client.py` for a client scoped to an account, run their call, and release it.
- Inputs and outputs of those helpers are Pydantic models from `schemas/` — no raw Telethon objects leaking up into the UI layer.

## Sessions

- One Telethon session file per account, stored under a configured directory (`settings.session_dir`).
- Session files are credentials — never log their contents, never commit them.
- A single session file must not be opened by two clients at once. The factory in `core/telegram_client.py` is the only safe entry point.

## Device fingerprints

- One immutable device fingerprint is stored per `account_id` in SQLite table `device_fingerprints`.
- `core/device_fingerprint.py` generates the random desktop profile only when no saved row exists.
- `core/telegram_client.py` first prepares a `TelegramClientProfile` Pydantic schema, then passes its device fields into `TelegramClient`.
- Device fields sent to Telethon: `device_model`, `system_version`, `app_version`, `lang_code`, `system_lang_code`.
- Existing device fingerprint rows are never updated by the fingerprint helper; duplicate inserts return the saved row.

## Session checks

- Session checks live in `core/telegram_client.py` as `check_telegram_session()`.
- The helper returns `TelegramSessionCheckResult`, never raw Telethon objects or raw dicts.
- It uses the saved device fingerprint and `receive_updates=False`.
- It never deletes `.session` files. Deletion/recovery decisions belong above the gateway.
- Permanent statuses: `alive`, `unauthorized`, `session_error`, `account_error`.
- Temporary/non-delete statuses: `network_error`, `proxy_error`, `flood_wait`, `unknown_error`.

## Proxies (python-socks)

- One proxy (SOCKS5 or HTTP) per account. Every Telethon client is constructed with that proxy via `python-socks`. Without a per-account IP, Telegram bans.
- The `account_id → proxy` mapping is stored in the DB; on-disk format (`.env` line, separate table, etc.) is [TO BE DETERMINED — decide alongside the account model].
- Proxy timeouts are logged as `WARNING` through `core/logging.py` (see `context/logging.md`).

## Rate limits and FloodWaitError

- Telegram returns `FloodWaitError` with a `seconds` field when you hit a limit. Catch it at the feature boundary, log a business event via `core/logging.py`, and either back off or reschedule via APScheduler.
- Never retry a flood-waited call immediately. The cooldown is per account, not global.

## Account lifecycle states

[TO BE DETERMINED — populate after first implementation. Expected states: `created`, `verified`, `warming`, `active`, `banned`. Decide the canonical enum and store it on the account row.]

## What does NOT belong here

- No direct DB writes from inside a Telethon callback — return a schema, persist in the feature.
- No UI calls from inside a Telethon coroutine — emit a NiceGUI notification from the feature handler that awaited it.
