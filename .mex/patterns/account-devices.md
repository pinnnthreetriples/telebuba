---
name: account-devices
description: Change an account's devices (desktop/mobile), the second sign-in or the mobile container.
last_updated: 2026-10-23
edges:
  - target: context/runtime-telegram.md
    condition: sessions, proxies or the client pool
  - target: context/runtime-warming.md
    condition: the warming device
---

# Account Devices

1. An account has one or two sign-ins (desktop and/or mobile), each with its own `.session` and fingerprint row; one session never presents two devices. Fingerprints are inserted once, never upserted.
2. The pool keys clients by account and device. A borrower naming no device gets the primary (desktop if the account has one); a named device the account lacks is refused with a stable code, never served by the other. Standing subscriptions live on, and move with, the primary.
3. Mobile connects with the Android app's own api credentials and `lang_pack="android"`; without them refused, never faked on desktop's. Desktop by default, mobile only by operator pick. A warming stint keeps its device over restarts. Mobile warms only on the pod: emulator off refuses its start, skips its visits; a start checks the host, then readies the phone (`_mobile_prep`).
4. A second device signs in with the code the existing session reads from 777000, only from a message newer than the id read just before the request, never by host clock. Phone and user id come from that session; code requests are paced per account. A failure revokes and deletes the new session, storing nothing.
5. Logout/reset end and forget the 2nd device; removal deletes both sessions under the account's auth lock.
6. Mobile is a Docker pod (off unless `EMULATOR__ENABLED`; `docs/redroid.md`): a net-gateway (hev-socks5-tunnel, fail-closed kill-switch, no IPv6, adb on 127.0.0.1) and redroid in its namespace, online only via the account's SOCKS5 proxy (HTTPS refused); the proxy enters as a copied file, never env/argv/logs. Android: the fingerprint's real build (`configs/oem_blueprints`; `build.prop` each cold boot) and chip (`configs/soc`: `ro.soc.*`, pinned cores, cpuinfo/cpufreq; new pods), proxy country's timezone and SIM. A pod on its current proxy (digest) never reboots; a proxy change stops it. adb: argv lists, text escaped or refused; taps only nodes its UI dump showed. A failed start undoes itself (a booted new pod is only stopped); a sign-in stopping short keeps the pod up (`sign_in_refusal`). Open stages feed the phone dialog. `EMULATOR__GPU_MODE` and opt-in shared /data (`EMULATOR__DATA_*`; its diff outlives a remade Android) are fixed at create; deletion removes both and its diff. Stealth: `setprop` only (no resetprop), never `adb_enabled 0`. First boot seeds contacts/photos once, never failing. scrcpy window only with `EMULATOR__DESKTOP_VIEWER`.
7. The device farm (`/devices`) lists mobile pods as phones, starts or sleeps them; a starting or warming one refuses stop. Web screen: a WebSocket running scrcpy-server only while open (killed with its forward), one viewer per phone. Server version = SPA decoder's (≤ 3.3.1). `control` holds the phone (`manual`) and excludes an activity session both ways. Each handshake needs exactly one trusted Origin.
8. Test both device shapes (desktop-first, mobile-first) and the refusal codes; i18n parity covers `DeviceRefusalCode`.
