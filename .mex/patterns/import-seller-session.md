---
name: import-seller-session
description: Change the seller .session + .json import, its device fingerprint, or recording a known 2FA password (after the proxy).
last_updated: 2026-10-26
edges:
  - target: context/runtime-telegram.md
    condition: sessions, proxies or the client pool
  - target: patterns/account-devices.md
    condition: the mobile device or the emulator
---

# Import Seller Session

1. A `.session` may come with the seller's `.json` (`core/session_json.py`, a pure parser). The password is kept out of `repr` and logs. The JSON phone must equal the file stem's digits (`session_json_phone_mismatch`), and a bad file is `session_json_invalid`.
2. An `sdk` starting with `Android` forces `session_device=mobile` and stores the seller device VERBATIM as the fingerprint (platform `android`, `system_version` like `Android 14`, not the minted `SDK 34`). Known limit: the emulator refuses to sign in when the pod's real SDK differs, so a seller device on another Android release will not boot there.
3. A password is stored only after Telegram verifies it: `VerifyTwoFactorPassword` is `getPassword`, then `account.getPasswordSettings` with the SRP proof, read-only on both sides. A wrong password is `twofa_current_password_invalid`; no cloud password is `twofa_not_enabled`.
4. The import makes NO Telegram call: the account has no proxy yet, and a proxyless client connects from the operator's own IP. The password is recorded later, once the wizard's proxy step assigned one: `POST /accounts/{id}/2fa/record-json` (the same JSON again; `session_json_no_password` when it has none) and `POST /accounts/{id}/2fa/record` (typed), both under the per-account 2FA lock. `record_account_twofa` refuses an account with no proxy (`twofa_proxy_missing`) before any call.
5. The wizard calls record-json for each paired row after the proxy step and before the second-device step, which signs in with the password. A refusal (a wrong password is `twofa_current_password_invalid`) is shown on the 2FA step and the account is not skipped there.
6. Test with fake values only; i18n parity covers `SessionJsonRefusalCode`.
