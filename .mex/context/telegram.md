---
name: telegram
description: Telegram gateway rules, sessions, and rate-limit handling.
triggers: [telegram, telethon, session, flood wait, action]
edges:
  - target: context/architecture.md
    condition: gateway placement
  - target: context/services.md
    condition: service orchestration
  - target: patterns/add-telegram-task.md
    condition: new Telegram action
last_updated: 2026-07-16
---

# Telegram
- Only `core/telegram_client/` imports Telethon, constructs clients, manages the pool/listener, or dispatches raw requests.
- Callers use typed Pydantic actions/results through the public gateway; no Telethon objects cross the boundary.
- Services decide policy and persist results; callbacks do not contain UI or business orchestration.
- Session files are credentials under the configured session directory: never commit, log, duplicate-open, or expose them.
- Device fingerprints are created once per account and remain immutable.
- Account proxies resolve through `accounts.proxy_id` to the shared `proxies` pool; credentials stay inside gateways.
- Flood/slow-mode/peer-limit families return classified typed outcomes; services persist cooldowns and do not retry immediately.
- Restart safety comes from persisted domain state and reconciliation, not a Telegram outbox.
- Patch gateway internals at their owning submodule in tests; application code imports the public package API.