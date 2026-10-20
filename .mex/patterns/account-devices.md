---
name: account-devices
description: Change an account's devices (desktop/mobile), the second sign-in or the mobile container.
last_updated: 2026-10-20
edges:
  - target: context/runtime-telegram.md
    condition: sessions, proxies or the client pool
  - target: context/runtime-warming.md
    condition: the warming device
---

# Account Devices

1. An account has one or two sign-ins — desktop and/or mobile — each its own `.session` and its own fingerprint row; one session never presents two devices. Fingerprints are inserted once, never upserted.
2. The pool keys clients by account and device. A borrower naming no device gets the primary (desktop when the account has one); a named device the account lacks is refused with a stable code, never served by the other one. Standing subscriptions live on the primary and move with it.
3. Mobile connects with the Android app's own api credentials and `lang_pack="android"`; without them refused, never faked on desktop's. Desktop is the default; mobile only by the operator's pick. A warming stint keeps its device across restarts. Mobile warms only on the pod: emulator off refuses its start, skips its visits; a start checks the host, then readies the phone (`_mobile_prep`).
4. A second device signs in with the code the existing session reads from 777000, taken only from a message newer than the id read right before the request — never a host-clock cut-off. Phone and user id come from that session; code requests are paced per account. A failure revokes and deletes the new session and stores nothing.
5. Logout/reset end and forget the second device; removal deletes both sessions under the account's auth lock.
6. The mobile device is a Docker pod (off unless `EMULATOR__ENABLED`; `docs/redroid.md`): a net-gateway (hev-socks5-tunnel, fail-closed nf_tables kill-switch, IPv6 off, adb on 127.0.0.1) and redroid in its namespace: Android reaches the net only via the account's SOCKS5 proxy (HTTPS refused). The proxy enters only as a `docker cp`'d file, never env, argv, logs or errors. Android is the fingerprint's real phone build (`configs/oem_blueprints`, `build.prop` each cold boot) with the proxy country's timezone and SIM. A pod on its current proxy (digest) is never rebooted; a proxy change stops it. adb: argv lists, text escaped or refused; nothing tapped the UI dump did not show. A failed start undoes itself (a booted new pod is only stopped), logged by code; an operator's sign-in that stops short keeps the pod up (`sign_in_refusal`). Open stages feed the Accounts phone dialog. Deletion removes both. First boot seeds contacts/photos once, never failing it. Desktop scrcpy window only with `EMULATOR__DESKTOP_VIEWER`.
7. The device farm (`/devices`) lists each mobile pod as a phone, starts it in the background or puts it to sleep; a starting or warming one is refused stop. Its web screen is a WebSocket running scrcpy-server only while open, killed with its forward on any exit; one viewer per phone. The server's version equals the SPA decoder's (@yume-chan/scrcpy ≤ 3.3.1). `control` holds the phone (`manual`) and excludes an activity session both ways. Every WebSocket handshake needs exactly one trusted Origin.
8. Test both device shapes (desktop-first, mobile-first) and the refusal codes; i18n parity covers `DeviceRefusalCode`.
