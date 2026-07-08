---
name: telegram
description: Telethon integration — session handling, typed actions + executor, crash safety, rate-limit classification, and the rules around touching Telegram from this codebase.
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
    condition: when the Telegram call is part of the runtime workflow
  - target: patterns/add-telegram-task.md
    condition: when adding any new Telethon-using action
last_updated: 2026-07-06
---

# Telegram (Telethon) Integration

## Where Telegram code lives

- All Telethon client construction, session checks, and action dispatch live in `core/telegram_client/`.
- The public API is re-exported from `core.telegram_client` so callers import from `core.telegram_client`, not private submodules.
- Only `core/` may import `telethon`.
- Services (and `api/`) never call raw client methods directly. They build a typed action and pass it to the executor: `await core.telegram_client.execute(account_id, action)`.
- Inputs and outputs of `execute` are Pydantic models from `schemas/telegram_actions.py`; no raw Telethon objects leak up.

## Gateway package layout

```text
core/telegram_client/
├── __init__.py        public API re-exports
├── _client.py         client construction + lifecycle
├── _pool.py           client pool: reuse and lifecycle management for concurrent account operations
├── _session.py        session liveness checks
├── _spam.py           account status probe helpers
├── _actions.py        typed action executor + dispatch
├── _media.py          profile media actions
├── _read.py           message read actions (fetch dialogs, read messages)
├── _read_stories.py   story read actions
├── _video.py          video/media download and upload actions
└── _util.py           shared helper code
```

Tests that patch internals should patch the submodule that owns the binding. App code should use the public API from `core.telegram_client`.

## Typed actions

`schemas/telegram_actions.py` declares every Telegram action as a Pydantic class. The executor pattern-matches on the concrete action model and calls the right gateway helper. Benefits:

- Services and tests describe *what* to do without touching Telethon.
- Validation at the boundary.
- One point to classify SDK errors and return typed results.

Implemented signature:

```python
async def execute(account_id: str, action: TelegramAction) -> ActionResult
```

`ActionResult` carries `status`, `action_type`, `account_id`, and optional fields such as `message_id`, `flood_wait_seconds`, `error_type`, and `error_message`. No Telethon object leaves the gateway.

Current important statuses include ordinary success/failure plus Telegram rate-limit family statuses such as `flood_wait`, `slow_mode_wait`, `premium_wait`, and `peer_flood`.

## Crash safety — direct executor, not an outbox

A `telegram_outbox` table was originally planned but was **not built and has been dropped**. At current single-process scale, the executor is called directly and durability comes from persisted service state, not a queue:

- `services/warming/` owns per-account async runtime tasks and writes cycle outcomes to `warming_account_state`.
- On restart, `reconcile_warming_runtime()` rebuilds the in-memory task set from stored state.
- Revisit a real outbox/queue only if execution moves to multi-process.

## Sessions

- One Telethon session file per account, stored under `settings.telegram.session_dir`.
- Session files are credentials — never log their contents, never commit them.
- A single session file must not be opened by two clients at once. The gateway is the only safe entry point.

## Device fingerprints

- One immutable device fingerprint is stored per `account_id` in SQLite table `device_fingerprints`.
- `core/device_fingerprint.py` generates a profile only when no saved row exists.
- `core.telegram_client` prepares a `TelegramClientProfile` Pydantic schema, then passes its fields into Telethon.
- Existing device fingerprint rows are never updated by the fingerprint helper; duplicate inserts return the saved row.

## Session checks

- Session checks are exposed as `core.telegram_client.check_telegram_session()`.
- The helper returns `TelegramSessionCheckResult`, never raw Telethon objects or raw dicts.
- It uses the saved device fingerprint and does not delete `.session` files.
- Deletion/recovery decisions belong above the gateway.
- Permanent statuses: `alive`, `unauthorized`, `session_error`, `account_error`.
- Temporary/non-delete statuses: `network_error`, `proxy_error`, `flood_wait`, `unknown_error`.

## Proxies

- The `account_id → proxy` mapping is stored in SQLite table `account_proxies`.
- Read models mask username/password presence; raw password is only used inside core gateways when constructing the proxy config.
- Proxy check results are persisted through repositories and surfaced through account read models.

## Rate-limit classification

- The executor catches the relevant Telethon rate-limit family and returns a typed `ActionResult` instead of leaking SDK exceptions upward.
- Callers persist cooldown/error state in their own domain tables when needed.
- Do not retry rate-limited actions immediately from the service layer.

## Account lifecycle states

- Session health is stored on `accounts.status` (`new`, `alive`, `unauthorized`, `session_error`, `account_error`, `flood_wait`, `network_error`, `proxy_error`, `unknown_error`).
- Warming runtime lifecycle lives separately in `warming_account_state.state` (`idle`, `active`, `sleeping`, `flood_wait`, `quarantine`, `error`).
- A unified business lifecycle (`created` / `verified` / `active` / `banned`) is still undecided; see `state/active.md` open decisions.

## What does NOT belong here

- No direct DB writes from inside a Telethon callback — return a schema/result, let the service persist.
- No UI calls from inside a Telethon coroutine — feature handlers render UI after awaiting services.
- No raw SDK calls outside `core.telegram_client.execute()` or gateway-owned helpers.
