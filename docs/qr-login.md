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
