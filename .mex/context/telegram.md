---
name: telegram
description: Telethon integration — session handling, typed actions + executor, outbox, rate limits, and the rules around touching Telegram from this codebase.
triggers:
  - "telegram"
  - "telethon"
  - "session"
  - "MTProto"
  - "account"
  - "flood wait"
  - "outbox"
  - "telegram action"
edges:
  - target: context/architecture.md
    condition: when placing Telegram code in the right layer
  - target: context/services.md
    condition: when the action lives inside a service (the usual case)
  - target: context/warming.md
    condition: when the Telegram call is being scheduled, not direct
  - target: patterns/add-telegram-task.md
    condition: when adding any new Telethon-using feature
last_updated: 2026-06-10
---

# Telegram (Telethon) Integration

## Where Telegram code lives

- All Telethon client construction and lifecycle is in `core/telegram_client.py`. Only `core/` may import `telethon`.
- Services and features never call `client.send_message(...)` directly. They build a typed action and pass it to the executor: `await core.telegram_client.execute(account_id, action)`.
- Inputs and outputs of `execute` are Pydantic models from `schemas/telegram_actions.py` (action types) and `schemas/<domain>.py` (results) — no raw Telethon objects leak up.

## Typed actions

`schemas/telegram_actions.py` declares every Telegram action as a Pydantic class:

```python
class TelegramAction(BaseModel): ...   # base; discriminator on action_type
class JoinChannel(TelegramAction):     action_type: Literal["join_channel"]; channel: str
class PostComment(TelegramAction):     action_type: Literal["post_comment"]; chat_id: int; text: str
class UpdateProfile(TelegramAction):   action_type: Literal["update_profile"]; first_name: str; last_name: str | None
```

The executor pattern-matches on `action_type` and calls the right Telethon method. Benefits:
- Services and tests describe *what* to do without touching Telethon.
- Validation at the boundary — no bad payloads slip into the SDK.
- One point to enforce rate limits, FloodWait policy, proxy config, and outbox writes.

[VERIFY AFTER FIRST IMPLEMENTATION — exact executor signature once `core/telegram_client.py` exists.]

## Outbox pattern

Telegram actions cost money (accounts can be banned). Mid-flight crashes must not leave the system in an unknown state.

- For non-trivial actions, services do not call `execute` directly. They write an **intent row** to the SQLite `telegram_outbox` table via `core/db.py`.
- A dedicated APScheduler job (or `services/telegram_outbox.py`) picks up pending intents, calls `execute`, records the result, and marks them `done` / `failed`.
- Survives crashes: on restart, pending intents are retried. Idempotency is the caller's contract (an intent has a `dedupe_key`).
- Trivial reads (fetching an account's profile to display) bypass the outbox — they are cheap to retry on a re-request.

[TO BE DETERMINED — outbox table schema, retry/backoff policy, dedupe key generation, worker owner.]

## Sessions

- One Telethon session file per account, stored under `settings.telegram.session_dir`.
- Session files are credentials — never log their contents, never commit them (covered by `.gitignore`).
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
- The `account_id → proxy` mapping is stored in the DB; on-disk format is [TO BE DETERMINED — decide alongside the account model].
- Proxy timeouts are logged as `WARNING` through `core/logging.py`.

## Rate limits and FloodWaitError

- Telethon raises `FloodWaitError` with a `seconds` field when a limit is hit. The executor catches it, logs a business event via `core/logging.py`, marks the outbox row `flood_wait` with `retry_after`, and returns a `FloodWaitResult` to the caller.
- Never retry a flood-waited call immediately. The cooldown is per account, not global. The outbox worker respects `retry_after`.

## Account lifecycle states

[TO BE DETERMINED — populate after first implementation. Expected: `created`, `verified`, `warming`, `active`, `banned`. Canonical enum lives in `schemas/accounts.py`, stored on the account row.]

## What does NOT belong here

- No direct DB writes from inside a Telethon callback — return a schema, let the service / executor persist.
- No UI calls from inside a Telethon coroutine — emit a NiceGUI notification from the feature handler that awaited the service.
- No raw `client.send_message(...)` calls outside `core/telegram_client.execute()` — every action must be a typed schema.
