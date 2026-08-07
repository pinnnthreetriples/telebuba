---
last_updated: 2026-08-06
---

# Telegram, Sessions and Proxies

- Only `core/telegram_client/` imports Telethon and owns pooled clients/listeners; services choose policy and persist outcomes. Never expose Telethon/session/tdata objects across the boundary.
- The client pool is the sole owner of an account connection that may be in service. Probes borrow from it; login/logout flows that must build directly are serialized separately. Account removal tombstones pool access for the full evict/unlink/delete sequence.
- Session filenames are one plain child of the session directory. Stored `session_name` overrides `account_id`; migration 7 enforces uniqueness. The lexical path guard is authoritative before filesystem resolution.
- Frozen accounts can remain authorized, so health classification uses Telegram freeze signals rather than `get_me()` alone. Rate-limit/frozen errors are converted to stable outcomes; services decide what state is durable.
- Device fingerprints are immutable. Proxy credentials are resolved inside `core/` from the shared pool; capacity is config-driven. Proxy checks discover the exit IP over TLS and persist geolocation consensus without exposing credentials.
- Profile/privacy writes use stable error codes. Telegram privacy `setPrivacy` replaces the key's entire rule vector, so applying a simplified level can discard exceptions and must remain an explicit operator action.
- An RPC success is not proof that every profile field became visible/stored. UI confirmation comes from a fresh read; do not persist a lag-sensitive "confirmed" verdict.
- Sending clients set `parse_mode=None` so operator text is not silently transformed. Markdown-like cleanup applies only to generated text.

API/frontend contain no Telegram runtime policy. Code, gateway tests and migrations are the detailed source of truth.
