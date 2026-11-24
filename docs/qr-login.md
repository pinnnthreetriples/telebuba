# QR-code sign-in

How the add-account wizard signs a new account in by QR code. The operator scans the code from Telegram on a phone that is already signed in (Settings → Devices → Link Desktop Device).

## Flow
1. `POST /accounts/start-qr-login` creates a placeholder `qr-<hex>` account. It has no phone and makes no connection. The id is never renamed; the session check fills the user id, phone and names after sign-in.
2. The operator assigns a proxy, so the first Telegram connection goes through it, as in the phone-code flow.
3. `POST /accounts/{id}/qr-login` opens the sign-in (`auth.exportLoginToken`, Telethon `qr_login`). The SPA polls `GET …/qr-login`, and a 2FA account answers through `POST …/qr-login/password`. The first `alive` read persists the account through `update_account_from_session_check` and starts its inbox.

## Gateway (`core/telegram_client/_qr_login.py`)
- One task per sign-in owns the client and the account's auth lock for its whole life, for the reason `_auth.py`'s docstring gives. It ends at `TELEGRAM__QR_LOGIN_TIMEOUT_SECONDS`, so a QR shown and forgotten cannot hold the lock.
- The following cancel the sign-in first: account removal, logout and reset, closing the wizard, and app shutdown.
- The client is built with updates ON: `QRLogin.wait` completes on `UpdateLoginToken`. When a token lapses (about 30 s), the task re-exports it and publishes the new image.
- The QR image is an SVG data URI rendered with `segno`. The token, the `tg://` URL and the password are never logged or stored, and failures are classified by exception class name only.

## Duplicate scan
If the scanned Telegram user is already in Telebuba under another id, the new authorization is logged out (`log_out_session`, wiping the file) and the placeholder row is dropped. The API answers `409 account_exists`. Telegram is also passed the fleet's user ids as `except_ids`.

`log_out_session` does not raise when it cannot confirm the logout: it returns a `network_error` result and leaves the file. Only a result with status `unauthorized` means the authorization is ended. Otherwise (a returned `network_error`, or an exception) the surplus authorization may still be alive on the existing account, so the session file is moved to the quarantine (`remove_account_session`, never unlinked: it is the only copy of that key) before the placeholder row is removed, and a WARNING `qr_login_surplus_session_left` is logged on the existing account (its id and the error class only) so the operator can close it from that account's Sessions card. The placeholder is still dropped and the API still answers `409 account_exists`.

## Only on a placeholder or a dead session

A QR sign-in writes the new key into the account's own session file. It is allowed only on a `qr-` placeholder (the "add by QR" flow) or on an account whose session check already said the key is gone (`unauthorized`, `session_error`). Any other account (an import never checked, one failing for another reason) is refused with `session_not_dead`: its file may hold a live key, and a duplicate would even be logged out.
