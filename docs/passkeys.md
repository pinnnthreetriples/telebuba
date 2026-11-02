# Passkeys: signing in without SMS

An account's passkey (`services/accounts/passkey.py`) lets Telebuba sign the account back in
when its session is gone and the rented number no longer receives codes.

## How Telegram takes it

Telegram accepts a WebAuthn credential from Telebuba's own api_id: RP ID `telegram.org`, origin
`https://telegram.org`, attestation `none` (`core/webauthn_passkey.py`).

- Registration borrows the pooled client (`core/telegram_client/_passkey.py`).
- Sign-in (`core/telegram_client/_passkey_login.py`) runs on the account's own `.session` under
  the auth lock and tombstone, like the phone-code login. It finishes on the DC the user handle
  names (`from_dc_id`/`from_auth_key_id` when that differs), then needs the stored 2FA password.
- An already-authorized session is left alone; a sign-in that lands on another user is logged
  out.

## Checking that Telegram still has it

Telegram can drop an account's passkey without telling Telebuba; the first sign is a
`PASSKEY_CREDENTIAL_NOT_FOUND` at sign-in, by which time the session is usually dead. The
«Проверить в Telegram» button on the «Ключ доступа» card (`POST /accounts/{id}/passkey/check`)
asks while the session is alive: one `account.getPasskeys` on the pooled client, looking for the
stored credential id. It is read-only and needs no vault.

- `present` / `missing`: only a successful Telegram list decides. A `missing` key is dead: delete
  it and create a new one.
- `unknown`: Telegram was not reached (`unavailable`) or refused the request (a dead session
  lands here as `passkey_telegram_refused`). It never reads as `missing`.
- A flood wait is refused with its retry-after, like the other passkey actions; an account
  without a stored passkey is refused `passkey_missing`.
- Nothing is changed: no status, stored row or vault key is touched, and it never runs by itself.

## Signing in to the account's web window

The account's web.telegram.org window (the globe button) answers WebK's «Log in by passkey»
from the same vault key, so a dead session can be revived there as well. The browser has no
such credential, so a document-start hook (`core/web_login/_passkey.py`) takes over
`navigator.credentials.get` for RP ID `telegram.org` only; every other WebAuthn call is left
to the browser. The window polls the hook over its CDP socket, signs the challenge in the
vault (`services/accounts/_web_passkey.py`) and resolves the promise with a synthetic
`PublicKeyCredential`. A vault failure rejects as `NotAllowedError`, like a cancelled prompt.

- Offered only when the vault is configured and the account has a passkey; it signs for that
  window's own account and nothing else.
- It is a page hook, not a Chrome extension: Chrome 137+ removed `--load-extension`, and the
  window is already driven over CDP. Polled, not pushed, because `Runtime.enable` would be a
  DevTools tell.
- The cloud password is still asked for by WebK when the account has one; the passkey must
  exist on Telegram's side too (`PASSKEY_CREDENTIAL_NOT_FOUND` means it was removed there).
- The window must be opened after the passkey exists: the hook is installed at launch.

## Where the operator creates one

- One account: the «Ключ доступа» card in the account editor.
- A batch: the add-account wizard's last step, offered only when the vault is configured
  (`PasskeyBulkStep.tsx`). It runs in the browser window one account at a time, 15–30 s apart,
  so a batch does not register every fresh account in the same second. An account that already
  has a passkey counts as done; a vault failure stops the batch.

## The key vault

The private key lives in an OpenBao (or Vault) Transit engine as a non-exportable `ecdsa-p256`
key (`core/passkey_vault.py`); Telebuba only ever sees the public half and signatures. An empty
`PASSKEY_VAULT__TOKEN` turns the feature off.

- Run one local node on `127.0.0.1:8200` with integrated `raft` storage (OpenBao 2.x has no
  `file` backend) and mount `transit`.
- Keep the mount for Telebuba alone and give it a token whose policy grants, by `+`
  (one path segment) rather than a trailing `*` (which also matches `<key>/rotate`):
  - `transit/keys/+`: create, read, update, delete, with `allowed_parameters` limited
    to `type = ecdsa-p256` and `exportable = false`;
  - `transit/keys/+/config`: update, `deletion_allowed = true` only;
  - `transit/sign/+/sha2-256`: update, with `input`, `marshaling_algorithm = asn1`
    and `key_version`.

  Never grant `rotate`, `trim`, `export`, `backup` or `restore`: Telegram holds only
  version 1's public key, so Telebuba signs with `key_version = 1` and refuses a key
  that has been rotated.
- The node starts sealed after every restart: unseal it with the key kept off the machine (a
  flash drive) before using passkeys.

Losing the vault data or the unseal key loses every passkey; the accounts then fall back to SMS
login.
