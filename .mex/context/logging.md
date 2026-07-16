---
name: logging
description: Logging, persisted events, Sentry, and SSE rules.
triggers: [logging, log_event, sentry, sse, events]
edges:
  - target: context/conventions.md
    condition: logging rule
  - target: context/architecture.md
    condition: event flow
last_updated: 2026-07-16
---

# Logging
- `core/logging.py` is the only loguru/Sentry entry point; never use `print()`.
- `debug.log` stores rotating diagnostics.
- SQLite `logs` stores compact structured business events through `log_event`.
- ERROR events may reach Sentry when configured.
- `/api/v1/logs` provides paginated history; `/api/v1/events` provides authenticated SSE updates.
- Backend emits stable event codes and structured parameters; the SPA localizes them.
- Logging is best-effort and must not break the business operation.
- Avoid large payloads and noisy per-item logging in bulk work.