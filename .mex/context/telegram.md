---
name: telegram
description: Telethon integration — session handling, typed actions + executor, crash safety, rate limits, and the rules around touching Telegram from this codebase.
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
last_updated: 2026-06-11
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
class UpdateProfile(TelegramAction):   action_type: Literal["update_profile"]; first_name: str; last_name: str | None; username: str | None; bio: str | None
```

The executor pattern-matches on `action_type` and calls the right Telethon method. Benefits:
- Services and tests describe *what* to do without touching Telethon.
- Validation at the boundary — no bad payloads slip into the SDK.
- One point to enforce rate limits, FloodWait policy, and proxy config.

Implemented signature (`core/telegram_client.py`):

```python
async def execute(account_id: str, action: TelegramAction) -> ActionResult
```

`ActionResult` carries `status` (`ok` / `failed` / `flood_wait`), `action_type`, `account_id`,
and optional `message_id` / `flood_wait_seconds` / `error_type` / `error_message`. No Telethon
object ever leaves the gateway.

## Crash safety — direct executor, not an outbox

An `telegram_outbox` table was originally planned for at-least-once intent delivery. It was
**not built and has been dropped** (see `context/decisions.md` → "Outbox pattern", Superseded).
At single-process / ~50-account scale the executor is called directly and durability comes from
**per-cycle persisted state**, not a queue:

- `services/warming.py` runs each account as an `asyncio.Task` and writes the outcome of every
  cycle to `warming_account_state` (last action/channel/error, heartbeat, FloodWait window).
- On restart, `reconcile_warming_runtime()` rebuilds the in-memory loop set from that table — a
  crash loses at most the in-flight action, never accumulated progress.
- Revisit a real outbox/queue only if we move to multi-process execution.

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
- The `account_id → proxy` mapping is stored in SQLite table `account_proxies`.
  Read models mask username/password presence; raw password is only used inside core gateways
  when constructing the Telethon proxy config.
- Proxy timeouts are logged as `WARNING` through `core/logging.py`.

## Rate limits and FloodWaitError

- Telethon raises `FloodWaitError` with a `seconds` field when a limit is hit. The executor catches it, logs a business event via `core/logging.py`, and returns an `ActionResult` with `status="flood_wait"` and `flood_wait_seconds` set. The caller persists the cooldown window onto `warming_account_state`.
- Never retry a flood-waited call immediately. The cooldown is per account, not global. The warming loop reads the stored FloodWait window before scheduling the next cycle.

## Account lifecycle states

[TO BE DETERMINED — populate after first implementation. Expected: `created`, `verified`, `warming`, `active`, `banned`. Canonical enum lives in `schemas/accounts.py`, stored on the account row.]

## What does NOT belong here

- No direct DB writes from inside a Telethon callback — return a schema, let the service / executor persist.
- No UI calls from inside a Telethon coroutine — emit a NiceGUI notification from the feature handler that awaited the service.
- No raw `client.send_message(...)` calls outside `core/telegram_client.execute()` — every action must be a typed schema.
