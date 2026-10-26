---
name: import-seller-session
description: Change the seller .session + .json import, its device fingerprint, or recording a known 2FA password.
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
4. The import records best-effort: a network failure never fails the import and an unverified password is never stored (`twofa_recorded=false`). `POST /accounts/{id}/2fa/record` does the same for an existing account, under the per-account 2FA lock.
5. Test with fake values only; i18n parity covers `SessionJsonRefusalCode`.
