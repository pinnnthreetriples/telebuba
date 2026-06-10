---
name: logging
description: Three-tier logging architecture — loguru file, SQLite logs table, NiceGUI Logs page. Load when emitting, querying, or displaying log entries.
triggers:
  - "logging"
  - "loguru"
  - "structlog"
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
    condition: when classifying a Telegram-side event (FloodWait, PeerFlood, etc.)
last_updated: 2026-06-10
---

# Logging

## Three tiers

1. **`debug.log` (loguru)** — rotating file, colorized output. Noisy diagnostic data: stacktraces, retries, timing.
2. **SQLite `logs` table (structlog)** — structured business events. The data that needs filtering and a UI.
3. **NiceGUI `Logs` page** — table fed from SQLite, refreshed every 3 seconds, filterable by account and status (`success` / `warning` / `error`).

All three are encapsulated in `core/logging.py`. There is no other entry point.

## Levels and what goes where

| Level     | Events |
|-----------|--------|
| **INFO**    | account login, joining a channel, posting a comment, profile update |
| **WARNING** | `FloodWaitError`, proxy timeout |
| **ERROR**   | `PeerFlood`, channel ban, invalid session |

In the SQLite `logs` table, level is normalized into `status`:
- `INFO`    → `success`
- `WARNING` → `warning`
- `ERROR`   → `error`

(used as the filter dropdown on the Logs page).

## Sentry

- `sentry-sdk` is initialized in `core/logging.py` if `SENTRY_DSN` is present in `.env`.
- Only `ERROR` and unhandled exceptions are sent. `INFO` / `WARNING` stay local.
- Sentry does not replace the SQLite `logs` table — it is a notification channel for prod (account down at night → alert).

## Usage rules

- Nothing outside `core/logging.py` imports `loguru`, `structlog`, or `sentry_sdk`.
- No `print()` — anywhere. For debugging tests, use pytest's `caplog`.
- In a feature: `log_event(level="INFO", account_id=..., event="join_channel", **extra)`. Exact signature: [VERIFY AFTER FIRST IMPLEMENTATION of `core/logging.py`].
- Bulk operations (warming loop over 50 accounts) log ONE aggregated event, not 50, or the table balloons.

## NiceGUI Logs page

- Source: `SELECT ... FROM logs ORDER BY created_at DESC LIMIT N`. Limit and pagination: [TO BE DETERMINED].
- Polling every 3 seconds via `ui.timer(3.0, ...)`. Not WebSocket push — polling is simpler and 3s latency is fine for ~50 accounts.
- Filters: dropdown by `account_id`, dropdown by `status`.
- The page lives in `features/logs.py` (its own feature file, like everything else).

## What does NOT belong here

- Metrics (latency, throughput) — not in scope yet. If needed, separate table or Prometheus — do not dump into `logs`.
- Audit trail (who changed what when) — a separate entity from logs, do not conflate.
- Telethon session files — those belong to `core/telegram_client.py`, not `core/logging.py`.
