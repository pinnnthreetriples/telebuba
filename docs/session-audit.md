# Session audit

Why: an imported account keeps working only while nobody else holds a session of it. Whoever does can end ours (terminate all other sessions) or kept a copy of the auth key. Telebuba never ends other sessions by itself; the audit shows who is there.

## What it reads

`account.getAuthorizations`, from each of the account's own pooled clients (desktop and/or mobile, each its own authorization). Telegram marks only the session that asked as `current`, so the entry each client sees as `current` is OURS and everything else is not. What a session looks like decides nothing: an imported seller session wears the seller's own device by design, so a look-alike is exactly the case a heuristic would hide.

If any one of the account's clients cannot answer, the whole read fails: a partial answer would list our own second session as foreign, and closing "foreign" sessions would then end it.

The Telegram app inside a mobile container (pod) is not one of our clients, so its authorization is not recognised as ours and lists as foreign. Closing is therefore always an explicit, confirmed operator action.

## When it runs

- On demand from the account's "Sessions" card.
- Once, right after the first `alive` session check of an account that has a proxy. Like the 2FA record it needs a proxy: the import flow stays offline, because a proxyless client connects from the operator's own IP.

## What is kept

- `account_session_audits`: the latest snapshot per account (it names other people's IP and device, so it is deleted with the account).
- `account_session_audit_events`: one row per audit (`checked_at`, `total`, `foreign_count`, `killed_count`), counts only.
- The lifecycle journal gets `foreign_sessions_found` when a foreign session shows up that the previous audit did not list, with counts only, and a `session_dead` row carries `last_audit_at` / `last_audit_total` / `last_audit_foreign`, so a death can be bracketed ("the last audit saw N sessions at T").

No log line, journal row or API error carries a session hash, IP or device.
