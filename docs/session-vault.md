# Session vault: copies of every account's key

A `.session` file is the account: the only copy of its auth key. Lose the file and the account is gone unless a login code or a passkey brings it back. So Telebuba keeps two safety nets.

## Encrypted copies

`core/session_vault.py`, driven by `services/session_backups.py`.

- **What**: every account's session files (the primary and a second device's), read with SQLite's online backup (consistent while a client holds the file), encrypted with Fernet and written as versions: `runtime/session_vault/<session file name>/<UTC time>-<sha12>.session.enc`. An unchanged file is not copied again. The first version (the account as it was bought) is kept for good, plus the newest `SESSION_VAULT__KEEP_VERSIONS` (10).
- **When**: right after anything writes a key (an import, a phone/QR/passkey sign-in, a re-key, a second device; read from the log bus), and in the session guard's pass for every account at most once per `SESSION_VAULT__BACKUP_INTERVAL_HOURS` (24 h).
- **Where else**: `SESSION_VAULT__MIRROR_DIR` writes every version to a second folder too. The copies are encrypted, so another disk or a synced cloud folder is fine and recommended: a copy on the same disk does not survive the disk. Every copy call also brings the mirror level with the primary folder: a version the mirror missed (the disk was away) is copied over on the next call, and a mirror failure never costs the primary copy.
- **A failed copy is retried.** An account counts as copied only when all its files were written without an error. After a failure it is tried again after `SESSION_VAULT__RETRY_MINUTES` (30), by the guard's pass (also when the failure came right after an import or a re-key), not only after a day. Each failure is logged as `session_backup_failed` and sent to the operator bot.
- **Off until a key is set.** With no `SESSION_VAULT__KEY` nothing is copied and the guard logs `session_vault_off` once per start (also sent to the operator bot).

### Turning it on (the owner)

1. `uv run python -m tools.session_vault new-key` prints a key.
2. Put it in `.env` as `SESSION_VAULT__KEY=...` and restart Telebuba.
3. Save the same key in a password manager, NOT on this computer only. Without the key no copy can be read; with the key and a copy, anyone holds the account.
4. Optional: `SESSION_VAULT__MIRROR_DIR=D:/telebuba-session-copies` (another disk or a synced folder).

Never commit the key, never paste it into a chat or a log.

### Restoring a key

1. `uv run python -m tools.session_vault list <session file name without .session>` lists the copies, oldest first.
2. `uv run python -m tools.session_vault restore <copy> <new file>.session` decrypts one into a NEW file; it never writes over an existing one.
3. Stop Telebuba, set the account's broken file aside, put the restored file in its place under the same name, start Telebuba and check the account.

## The quarantine

Removing a session file (deleting an account, a confirmed reset, a discarded sign-in) moves it to `runtime/session_quarantine/<UTC time>-<name>.session` instead of deleting it. The session guard's pass purges what is older than `SESSION_VAULT__QUARANTINE_DAYS` (30). A file removed by mistake can be put back by hand within that window.

## A session file that is gone

Telethon makes an empty database at a path that holds none and then writes a fresh, unregistered key into it, which used to read as "Telegram revoked the key" and blocked restoring the real file. Now:

- **Never silently replaced.** The client pool (and `telegram_client`) look at the file first, read-only (`core/session_file.py`). A missing file, or one with no auth key, is refused before Telethon runs: nothing is created and nothing is asked of Telegram. A file that cannot be read right now, or is not a Telethon session, goes on as before. The phone-code, QR, passkey, second-device and re-key sign-ins build their own clients and still create or replace a key.
- **The verdict is `session_file_missing`**, not `unauthorized`: the account is out of service (no inbox, warming or campaigns, like `unauthorized`) but is not a death in the survival journal and sends no "session dead" message. It is logged as `account_session_file_missing` and told to the operator bot ("restore the file from a copy, or sign in by code"). A login code, QR or passkey is allowed for it as for `unauthorized`. An account whose stored status is `new` (never signed in) or `unauthorized` (a reset wipes the file on purpose) keeps the verdict `unauthorized`.
- **Restore** as above (`restore` into a new file, put it back under the same name, check the account).
- **At the start** the session guard's first pass compares the accounts with the sessions folder: each account (not `new`/`unauthorized`) whose file is missing or keyless is logged as `account_session_file_missing`, and `session_orphan_files_found` carries how many `.session` files belong to no account (re-key scratch files are ignored). Nothing is deleted or repaired.

### Importing a `.session`

An uploaded file is written under a temporary name (`<name>.session.importing`), proven a Telethon session holding a key, then renamed onto its final name, so a crash never leaves a half file under the real name and a leftover temporary file never blocks the next import. Anything else is refused with `session_file_invalid`. A failed import (the row could not be saved) moves the file to the quarantine instead of deleting it. If saving the seller's 2FA password fails after the import succeeded, the account is still returned and `account_twofa_store_failed` is logged; the wizard's record-json step stores it later.

## The database

The database is backed up as well (`DB__BACKUP_ENABLED`, on by default): it holds the 2FA passwords every re-login needs.

- **When**: `backups/telebuba-<UTC time>.db`, a consistent `VACUUM INTO` copy, at most every `DB__BACKUP_INTERVAL_HOURS` (24), the newest `DB__BACKUP_KEEP` (7) kept. The loop runs one **right at start** when the newest backup is missing or older than the interval, so a computer that restarts more often than daily is still backed up. After a failed run the next try is `DB__BACKUP_RETRY_MINUTES` (15) later (`db_maintenance_failed`, also sent to the operator bot), not a whole interval.
- **Before a schema change**: when an existing database has a migration still to apply, a snapshot goes into the same folder first (same rules, same file names, covered by the same retention). If that snapshot cannot be written the start fails and nothing is migrated: this is the credential store. A fresh database, or one with nothing to migrate, takes none. An engine whose migration failed is never kept; the next call starts again from scratch.
- **Where to look**: `GET /session-guard` carries `backups`: whether the vault and the mirror are on, how many accounts have no copy yet, when the newest session copy and the newest database backup were made, whether the database backup is overdue (none, or older than two intervals), and the key vault's last known health. The guard asks the vault's own `/v1/sys/health` once per pass and logs `passkey_vault_unhealthy` only when the state changes to a bad one.

## One owner of the fleet

Copies of the session files on a second computer (a laptop) are a backup only while that computer never connects them: two instances on one key, from two IPs, get the key revoked. `INSTANCE__FLEET_OWNER_HOST` names the one computer (its hostname) that may build Telegram clients. Set it on every computer that holds copies; on any other the client factory refuses (`FleetOwnerRefusedError`, read by every caller as an unreachable account) and the startup log says `instance_not_fleet_owner`. Empty means no restriction.

## Never without a proxy

An account without a proxy is never connected to Telegram: it would connect from this computer's own IP, the same for every such account. The client factory refuses (`ProxyRequiredError`), a session check is refused before it writes anything (`proxy_required`), and the inbox does not start for such an account. `TELEGRAM__ALLOW_DIRECT_CONNECTION=true` lifts this, for a deliberate local test only.
