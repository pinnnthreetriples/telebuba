---
name: account-devices
description: Change an account's devices (desktop/mobile), the second sign-in or the mobile container.
last_updated: 2026-10-24
edges:
  - target: context/runtime-telegram.md
    condition: sessions, proxies or the client pool
  - target: context/runtime-warming.md
    condition: the warming device
---

# Account Devices

1. An account has one or two sign-ins (desktop and/or mobile), each with its own `.session` and fingerprint row; one session never presents two devices. Fingerprints are inserted once, never upserted.
2. The pool keys clients by account and device. A borrower naming no device gets the primary (desktop if the account has one); a named device the account lacks is refused with a stable code, never served by the other. Standing subscriptions live on, and move with, the primary.
3. Mobile connects with the Android app's api credentials and `lang_pack="android"`; else refused, never faked. Desktop by default, mobile by operator pick. A warming stint keeps its device over restarts. Mobile warms only on the pod: emulator off refuses its start, skips its visits; a start checks the host, then readies the phone.
4. A second device signs in with the code the existing session reads from 777000, only from a message newer than the id read just before the request, never by host clock. Phone and user id come from that session; code requests are paced per account. A failure revokes and deletes the new session, storing nothing.
5. Logout/reset end and forget the 2nd device; removal deletes both sessions under the account's auth lock.
6. Mobile is a Docker pod (off unless `EMULATOR__ENABLED`; `docs/redroid.md`): a net-gateway (hev-socks5-tunnel, fail-closed kill-switch, no IPv6, adb on 127.0.0.1) and redroid in its namespace, online only via its SOCKS5 (HTTPS refused); proxy a copied file, never env/argv/logs. Android: the fingerprint's real build with board/builder/date (`configs/oem_blueprints`; `build.prop` per cold boot), chip (`configs/soc`: `ro.soc.*`, pinned cores, cpuinfo/cpufreq) and `ro.hardware` (own entrypoint) on new pods, `_features`/`_sensors`, proxy country's TZ and SIM. A pod on its current proxy never reboots; a new proxy stops it. adb: argv lists, escaped text, taps only on dumped nodes. A failed start undoes itself (a booted new pod or one on a kept diff just stops); a short sign-in keeps it up (`sign_in_refusal`). `EMULATOR__GPU_MODE` and opt-in shared /data (`EMULATOR__DATA_*`; its diff outlives a remade Android) are fixed at create; deletion: both+diff. Stealth: `setprop` only, never `adb_enabled 0`; keep `su` (adb is shell); battery `present` last. First boot seeds contacts/photos once, never fails. scrcpy window only via `EMULATOR__DESKTOP_VIEWER`.
7. The device farm (`/devices`) lists mobile pods as phones, starts or sleeps them; starting/warming ones refuse stop. Web screen: a WebSocket running scrcpy-server only while open (killed on exit), one viewer per phone. Server version = SPA decoder's (≤ 3.3.1). `control` holds the phone (`manual`) and excludes activity both ways; an operator's open holds it from warming (no park/stop) until Stop or control ends. Handshakes need exactly one trusted Origin.
8. Test both device shapes (desktop-, mobile-first) and the refusal codes; i18n parity covers `DeviceRefusalCode`.
