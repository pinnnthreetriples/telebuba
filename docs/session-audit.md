# Session audit

Why: an imported account keeps working only while nobody else holds a session of it. Whoever does can end ours (terminate all other sessions) or kept a copy of the auth key. Telebuba never ends other sessions by itself; the audit shows who is there.

## What it reads

`account.getAuthorizations`, from each of the account's own pooled clients (desktop and/or mobile, each its own authorization). Telegram marks only the session that asked as `current`, and lists it with `hash=0`; every other entry carries its real, stable hash, the same from every viewer. So the `current` entry is OURS, and another device of ours is found by difference: its real hash is the one entry the primary lists that the device itself does not list besides itself. If that is not exactly one entry, the read fails. `0` is never a key of any other entry, and is never closed. What a session looks like decides nothing: an imported seller session wears the seller's own device by design, so a look-alike is exactly the case a heuristic would hide.

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

## Closing foreign sessions

An explicit operator action: the card's "Close foreign sessions" (a confirm dialog lists exactly what will go), and a "Sessions" tab in the bulk editor for several accounts.

- One `account.resetAuthorization` per session, **never** `auth.resetAuthorizations`: an account can hold a second sign-in of its own, and that call would end it.
- The list is read again first. A hash that is no longer listed is already gone; one that is ours is refused whatever was asked. This also covers a session the operator confirmed that turned out to be ours.
- Telegram refuses the call from a session younger than 24 hours (`FRESH_RESET_AUTHORISATION_FORBIDDEN`, [account.resetAuthorization](https://core.telegram.org/method/account.resetAuthorization)). The imported seller session is old, so it can. That refusal ends the run and is reported as `too_new` with the time left, not as an error. A flood wait does the same.
- The calls are paced: the service draws a human-like pause for every gap (`SESSION_AUDIT__CLOSE_PAUSE_*`); core holds no randomness.
- Afterwards the account is audited again, so the screen shows what Telegram lists. The count is kept in the audit history (`killed_count`) and journalled (`foreign_sessions_closed`, counts only).
- The Telegram app inside a mobile container is not recognised as ours, so it lists as foreign and would be closed too if the operator confirms it.

## Re-key: a login the seller never had a copy of

An imported `.session` shares its auth key with whoever kept a copy. "Re-key the session" (card, and a bulk tab; never automatic) signs the account in again as a session of its own:

1. A new client with its own scratch session file (the account's stored device fingerprint and proxy, so Telegram sees the same device) calls `auth.exportLoginToken`; the imported session calls `auth.acceptLoginToken` with that token. If the new authorization comes back flagged `unconfirmed`, the imported session confirms it (`account.changeAuthorizationSettings`).
2. If the account has a cloud password, the new login finishes with the stored one (`SESSION_PASSWORD_NEEDED` after the token is accepted); without it the re-key is refused (`rekey_twofa_missing`) and nothing changed. The password is only passed to Telegram.
3. The new session must be the same Telegram user. Then the files are swapped: the imported file is set aside (not deleted), the new one is opened once from its final place and must answer as the same user, and only then is the imported file deleted. Any failure puts the imported file back and ends the new authorization.
3a. The imported session cannot name its own key (it sees itself as `0`), so the old hash is read from the NEW session after it signed in and before the swap: the one non-zero hash it lists that the imported session's list (read before the token) did not contain, and, when the imported session's entry has a creation date, one created at that time. Zero or several candidates, or a date mismatch, refuse the re-key with `rekey_old_key_unknown` through the normal failure path: the new login is ended and the imported session stays.
4. The old authorization (the seller-known key) is ended with the same single-hash call as "close foreign sessions". If Telegram refuses (a young session cannot end others), the old hash is stored with the time to try again; a background pass (`services/session_guard`) finishes it and the card says when.

A row can end in a terminal `problem` (`account_rekeys.problem`) instead of a retry: `old_key_unknown` (a row an earlier build stored with the old hash `0` that could not be resolved to exactly one session: not ours, not current, created before the re-key and last active within the day before it up to 5 minutes after) and `old_key_is_ours` (the close refused the stored hash as one of our own). Telebuba stops retrying, logs a warning, the card asks the operator to close the old session by hand, and a new re-key is refused (`rekey_needs_attention`) because the old key is still alive. A flood wait on the read is honoured for the next try.

One device: the account's primary session. A second sign-in of its own has its own key and is left alone. The account must have a proxy and be free of other features (warming, campaigns) while its client is swapped.

### What the Telegram docs say about the two open questions

Verified against the pages on 2026-11-02, not assumed:

- **Can the new session end the old one at once?** The docs only give the rule in the error: `FRESH_RESET_AUTHORISATION_FORBIDDEN` — "You can't logout other sessions if less than 24 hours have passed since you logged on the current session" ([account.resetAuthorization](https://core.telegram.org/method/account.resetAuthorization)). Nothing says that a session created by `auth.acceptLoginToken` ([method](https://core.telegram.org/method/auth.acceptLoginToken), [QR login](https://core.telegram.org/api/qr-login)) is exempt or confirmed ahead of that. So the implementation tries at once and, if refused, schedules the retry from the answer; both outcomes are handled and tested, and which one happens is for the first live run to show.
- **2FA interplay.** Neither method page, nor the QR login page, nor [api/auth](https://core.telegram.org/api/auth) says what a token login does for an account with a cloud password (`Authorization.password_pending` exists but is unexplained). The implementation follows Telethon's flow: the second `auth.exportLoginToken` raises `SessionPasswordNeededError`, and `sign_in(password=...)` finishes it.
- **Unconfirmed.** `Authorization.unconfirmed` and `account.changeAuthorizationSettings(confirmed)` are documented ([api/auth](https://core.telegram.org/api/auth), login confirmation); what an unconfirmed session may not do is not.

**Not exercised against Telegram.** Only fakes and a fake file system run in the tests. A live run on one throwaway account must show: the token login works through the account's proxy with its stored fingerprint, the cloud-password finish, whether the new session may end the old one at once, and that Telegram's active-sessions list then shows only the new device.

## The watch: continuous, cheap, paced

`services/session_guard` runs one background task (started in the lifespan beside the survival probes, stopped before the client pool; one uvicorn worker). Every pass (`SESSION_AUDIT__GUARD_INTERVAL_SECONDS`, 5 min) it ends old re-key authorizations that are due and re-audits **at most one** account.

- Which accounts: alive, behind a proxy, and not held by another feature (warming, campaigns, a channel-search loan). The first pass waits after startup (`WATCH_STARTUP_DELAY_SECONDS`) so a restart does not read the fleet at once.
- When: each account about every 6 to 12 hours (`WATCH_MIN_HOURS` / `WATCH_MAX_HOURS`). The interval is drawn afresh per account from a clipped log-normal in the service (core holds no randomness), so a fleet imported together never reads in step. An account never audited is due at once; one audited before a restart is due one drawn interval after that audit. A refused read is retried after `WATCH_RETRY_HOURS`.
- Proxies: no two accounts behind the same proxy within `WATCH_PROXY_SPACING_SECONDS`.
- A refusal that means the session is gone (`session_dead`, `account_deactivated`, `account_frozen`) goes through the normal session check, which records the status and journals `session_dead` together with the last audit before it, so the death is bracketed.
- A **new** foreign session (one the previous audit did not list) is journalled (`foreign_sessions_found`), logged (`session_audit_foreign_found`), sent to the operator bot (the existing notification channel, kind `foreign_session`) and shown live: the accounts list's badge and an open Sessions card refresh from the log stream.
- If the account has a stored passkey, the watch asks Telegram whether it still lists it (`check_account_passkey`, read-only). A passkey that is gone is logged (`session_watch_passkey_missing`) and sent to the operator bot (kind `passkey_missing`): somebody else was in the account's security settings.

Switch it off with `SESSION_AUDIT__WATCH_ENABLED=false`; the old-key closing keeps running.
