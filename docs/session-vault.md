# Session vault: copies of every account's key

A `.session` file is the account: the only copy of its auth key. Lose the file and the account is gone unless a login code or a passkey brings it back. So Telebuba keeps two safety nets.

## Encrypted copies

`core/session_vault.py`, driven by `services/session_backups.py`.

- **What**: every account's session files (the primary and a second device's), read with SQLite's online backup (consistent while a client holds the file), encrypted with Fernet and written as versions: `runtime/session_vault/<session file name>/<UTC time>-<sha12>.session.enc`. An unchanged file is not copied again. The first version (the account as it was bought) is kept for good, plus the newest `SESSION_VAULT__KEEP_VERSIONS` (10).
- **When**: right after anything writes a key (an import, a phone/QR/passkey sign-in, a re-key, a second device; read from the log bus), and in the session guard's pass for every account at most once per `SESSION_VAULT__BACKUP_INTERVAL_HOURS` (24 h).
- **Where else**: `SESSION_VAULT__MIRROR_DIR` writes every version to a second folder too. The copies are encrypted, so another disk or a synced cloud folder is fine and recommended: a copy on the same disk does not survive the disk.
- **Off until a key is set.** With no `SESSION_VAULT__KEY` nothing is copied and the guard logs `session_vault_off` once per start.

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

The database is backed up as well (`DB__BACKUP_ENABLED`, on by default): it holds the 2FA passwords every re-login needs.
