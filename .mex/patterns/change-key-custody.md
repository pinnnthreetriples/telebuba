---
name: change-key-custody
description: Change anything that creates, replaces, ends or restores an account's Telegram auth key (re-key, second device, sign-ins, web and phone logins, session restore).
last_updated: 2026-11-24
edges:
  - target: context/runtime-telegram.md
    condition: sessions, proxies or the client pool
  - target: patterns/import-seller-session.md
    condition: the seller import or recording a 2FA password
---

# Change Key Custody

1. A flow that makes a NEW auth key writes an intent first (`account_key_intents`, `core/repositories/account_key_intents.py`) and closes it at the end; an intent left open is settled by `services/accounts/_key_repair.py` (the session guard, and before an operator's retry). A new flow adds its own repair branch and a crash test per stage.
2. A session file is removed only on PROOF its authorization is ended: logout answered, or a proven-dead-key error. Unproven keeps the file and the open intent. The only copy of a key is quarantined, never deleted.
3. "Not authorized" comes only from `probe_authorized` (a 401). Telethon's `is_user_authorized()` answers False on a flood wait or a server error: never act on it.
4. A temporary check result never rewrites `accounts.status`: it goes to `link_status`. Decide "the key is gone" from `KEY_GONE_STATUSES` only.
5. An authorization Telebuba creates outside Telethon (web login, the phone app) is recorded in `account_own_authorizations` (`web`, `phone`), or the audit shows it foreign and it can be closed.
6. Cleanup of things an account leaves behind (browser profile, phone pod) is driven by a tombstone the deletion wrote, never by "not in the database": a wrong or empty database must delete nothing.
7. Every network await is bounded; an answer that never came is "maybe yes" (undo it), only an RPC refusal is "no".
8. Docs: `docs/session-audit.md`, `docs/session-vault.md`, `docs/redroid.md`.
