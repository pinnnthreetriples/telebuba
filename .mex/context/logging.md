---
name: logging
description: Three-tier logging architecture — loguru file, SQLite logs table, React Logs page over /api/v1/logs. Load when emitting, querying, or displaying log entries.
triggers:
  - "logging"
  - "loguru"
  - "log_event"
  - "logs page"
  - "INFO"
  - "WARNING"
  - "ERROR"
  - "sentry"
edges:
  - target: context/architecture.md
    condition: when placing logging code in the right layer
  - target: context/conventions.md
    condition: when the question is the rule (no print, gateway via core/logging.py)
  - target: context/telegram.md
    condition: when classifying a Telegram-side event
last_updated: 2026-06-28
---

# Logging

## Three tiers

1. **`debug.log` (loguru)** — rotating file. Diagnostic data: stacktraces, retries, timings.
2. **SQLite `logs` table** — structured business events persisted through `core.db.insert_log_row`.
3. **React `Logs` page** — table fed by the SPA from `GET /api/v1/logs`, refreshed by polling
   (SSE live tail lands in issue #174), filterable by account/status.

Optional **Sentry** reporting is also configured inside `core/logging.py` for ERROR events when `LOGGING__SENTRY_DSN` is set.

All logging is encapsulated in `core/logging.py`. There is no other entry point.

## Levels and what goes where

| Level | Events |
| --- | --- |
| **INFO** | normal business events |
| **WARNING** | recoverable operational problems |
| **ERROR** | failed operations / unexpected exceptions |

In the SQLite `logs` table, level is normalized into `status`:
- `INFO` → `success`
- `WARNING` → `warning`
- `ERROR` → `error`

## `log_event` signature

```python
async def log_event(
    level: LogLevel,
    event: str,
    account_id: str | None = None,
    extra: dict[str, object] | None = None,
) -> None:
    ...
```

`extra` is an open key/value bag. Keep payloads compact — large blobs bloat the `logs` table.

`log_event` is **best-effort**: a failure writing to SQLite or sending to Sentry is logged to loguru and swallowed, so business operations cannot be broken by a logging fault.

## Sentry

- `sentry-sdk` is initialized in `core/logging.py` if `settings.logging.sentry_dsn` is present.
- Only `ERROR` events are sent by `log_event`.
- Sentry does not replace the SQLite `logs` table — it is a notification channel for production issues.

## Usage rules

- Nothing outside `core/logging.py` imports `loguru` or `sentry_sdk`.
- No `structlog` in the current architecture.
- No `print()` anywhere. For debugging tests, use pytest facilities.
- In an api route/service: `await log_event("INFO", "event_name", account_id=account_id, extra={...})`.
- Bulk operations should aggregate where possible before logging, or the table becomes noisy.

## React Logs page

- Source: `GET /api/v1/logs` (an `api/v1/logs.py` route → `services/logs.py` → repository query
  over SQLite `logs`), newest first, cursor-paginated via `Page[T]`.
- Polling first (TanStack Query `refetchInterval`); SSE live tail is issue #174.
- Responses are **locale-neutral**: rows carry a stable event **code + structured params**, not
  pre-translated text — the SPA localizes via i18n (`context/frontend.md`). Carrying codes is a
  **staged refinement**, not day-one; existing free-text events keep working until migrated.
- Filters (account/status/activity) are query params on the route; the table UI lives in the
  frontend `pages/logs` slice.

## What does NOT belong here

- Metrics (latency, throughput) — not in scope yet. If needed, separate table or Prometheus; do not dump into `logs`.
- Audit trail (who changed what when) — a separate entity from logs, do not conflate.
- Telethon session files — those belong to the Telegram gateway, not logging.
