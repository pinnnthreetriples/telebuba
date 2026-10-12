---
name: account-devices
description: Change an account's devices (desktop/mobile), the second sign-in or the mobile container.
last_updated: 2026-10-12
edges:
  - target: context/runtime-telegram.md
    condition: sessions, proxies or the client pool
  - target: context/runtime-warming.md
    condition: the warming device
---

# Account Devices

1. An account has one or two sign-ins — desktop and/or mobile — each its own `.session` and its own fingerprint row; one session never presents two devices. Fingerprints are inserted once, never upserted.
2. The pool keys clients by account and device. A borrower naming no device gets the primary (desktop when the account has one); a named device the account lacks is refused with a stable code, never served by the other one. Standing subscriptions live on the primary and move with it.
3. Mobile connects with the Android app's own api credentials and `lang_pack="android"`; without them it is refused, never faked on the desktop credentials. Desktop is the default everywhere; mobile only by the operator's pick. A warming stint keeps the device it started on across restarts.
4. A second device signs in with the code the existing session reads from 777000, taken only from a message newer than the id read right before the request — never a host-clock cut-off. Phone and user id come from that session. Code requests are paced per account. A failure revokes and deletes the new session and stores nothing.
5. Logout/reset also end the second device and forget it; removal deletes both sessions under the account's auth lock.
6. Test both device shapes (desktop-first, mobile-first) and the refusal codes; i18n parity covers `DeviceRefusalCode`.
