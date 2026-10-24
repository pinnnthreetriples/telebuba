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

## The key vault

The private key lives in an OpenBao (or Vault) Transit engine as a non-exportable `ecdsa-p256`
key (`core/passkey_vault.py`); Telebuba only ever sees the public half and signatures. An empty
`PASSKEY_VAULT__TOKEN` turns the feature off.

- Run one local node on `127.0.0.1:8200` with integrated `raft` storage (OpenBao 2.x has no
  `file` backend) and mount `transit`.
- Give Telebuba a token whose policy covers only `transit/keys/tb-*`,
  `transit/keys/tb-*/config` and `transit/sign/tb-*`.
- The node starts sealed after every restart: unseal it with the key kept off the machine (a
  flash drive) before using passkeys.

Losing the vault data or the unseal key loses every passkey; the accounts then fall back to SMS
login.
