---
name: account-devices
description: Change an account's devices (desktop/mobile), the second sign-in or the mobile container.
last_updated: 2026-10-19
edges:
  - target: context/runtime-telegram.md
    condition: sessions, proxies or the client pool
  - target: context/runtime-warming.md
    condition: the warming device
---

# Account Devices

1. An account has one or two sign-ins — desktop and/or mobile — each its own `.session` and its own fingerprint row; one session never presents two devices. Fingerprints are inserted once, never upserted.
2. The pool keys clients by account and device. A borrower naming no device gets the primary (desktop when the account has one); a named device the account lacks is refused with a stable code, never served by the other one. Standing subscriptions live on the primary and move with it.
3. Mobile connects with the Android app's own api credentials and `lang_pack="android"`; without them it is refused, never faked on the desktop credentials. Desktop is the default everywhere; mobile only by the operator's pick. A warming stint keeps the device it started on across restarts. A mobile stint warms only through the emulator pod: with `EMULATOR__ENABLED` off its start is refused (`mobile_emulator_disabled`), and a running one skips each visit with `warming_mobile_emulator_off` and the card's `mobile_emulator_off` flag, never a Telethon cycle in its place.
4. A second device signs in with the code the existing session reads from 777000, taken only from a message newer than the id read right before the request — never a host-clock cut-off. Phone and user id come from that session. Code requests are paced per account. A failure revokes and deletes the new session and stores nothing.
5. Logout/reset also end the second device and forget it; removal deletes both sessions under the account's auth lock.
6. The mobile device is a Docker pod (off unless `EMULATOR__ENABLED`; setup and detail in `docs/redroid.md`): a net-gateway container (hev-socks5-tunnel, fail-closed nf_tables kill-switch, IPv6 off, adb on 127.0.0.1 only) and redroid in its network namespace, so Android reaches the net only through the account's SOCKS5 proxy (HTTPS proxies refused). The proxy enters the gateway only as a `docker cp`'d file, never env, argv, logs or errors. Android presents the fingerprint's phone as a real build of it (`configs/oem_blueprints`, written to `build.prop` before every cold boot) with the proxy country's timezone and SIM. A pod on the account's current proxy (by digest) is never rebooted or reconfigured; a proxy change stops it. adb commands are argv lists, text escaped or refused; nothing is tapped that the UI dump did not show. A failed start undoes itself; deleting the account removes both containers. The desktop scrcpy window opens only with `EMULATOR__DESKTOP_VIEWER`. First boot seeds contacts and photos once; seeding never fails the open.
7. The device farm (`/devices`) lists each mobile pod as a phone, starts it in the background or puts it to sleep; a starting or warming phone is refused stop. Its web screen is a WebSocket that runs scrcpy-server on the phone only while the socket is open, killed with its forward on any exit; one viewer per phone. The server's version must equal the SPA decoder's (@yume-chan/scrcpy, at most 3.3.1). `control` holds the phone (`manual`) and excludes an activity session both ways. Every WebSocket handshake needs exactly one trusted Origin.
8. Test both device shapes (desktop-first, mobile-first) and the refusal codes; i18n parity covers `DeviceRefusalCode`.
