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
5. Logout/reset also end the second device and forget it; removal deletes both sessions under the account's auth lock.
6. The mobile device is a Docker pod (off unless `EMULATOR__ENABLED`; `docs/redroid.md`): a net-gateway container (hev-socks5-tunnel, fail-closed nf_tables kill-switch, IPv6 off, adb on 127.0.0.1 only) and redroid in its network namespace, so Android reaches the net only via the account's SOCKS5 proxy (HTTPS proxies refused). The proxy enters only as a `docker cp`'d file, never env, argv, logs or errors. Android is a real build of the fingerprint's phone (`configs/oem_blueprints`, in `build.prop` at every cold boot) with the proxy country's timezone and SIM. A pod on its current proxy (by digest) is never rebooted or reconfigured; a proxy change stops it. adb: argv lists, text escaped or refused; nothing tapped the UI dump did not show. A failed start undoes itself (a new pod past its SDK check is only stopped) and is logged with its code, whatever refused it; but the operator's open whose sign-in stops short (no code to read, cooldown, unexpected screen) keeps the pod up and answers `sign_in_refusal`, to be finished by hand (warming's still fails). Each stage of an open is reported in memory; the Accounts page's phone dialog polls it over the live web screen (view while it signs in, control after). Deletion removes both. First boot seeds contacts/photos once, never failing the open. Desktop scrcpy window only with `EMULATOR__DESKTOP_VIEWER`.
7. The device farm (`/devices`) lists each mobile pod as a phone, starts it in the background or puts it to sleep; a starting or warming phone is refused stop. Its web screen is a WebSocket that runs scrcpy-server on the phone only while the socket is open, killed with its forward on any exit; one viewer per phone. The server's version must equal the SPA decoder's (@yume-chan/scrcpy, at most 3.3.1). `control` holds the phone (`manual`) and excludes an activity session both ways. Every WebSocket handshake needs exactly one trusted Origin.
8. Test both device shapes (desktop-first, mobile-first) and the refusal codes; i18n parity covers `DeviceRefusalCode`.
