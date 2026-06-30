---
name: proxy
description: Proxy-pool domain — shared first-class proxies that serve up to N accounts, the single source of truth for all proxy config. Load when touching proxy assignment, the pool UI, or Telegram connection routing.
triggers:
  - "proxy"
  - "прокси"
  - "пул"
  - "pool"
  - "socks5"
  - "exit ip"
edges:
  - target: context/decisions.md
    condition: the proxy-pool model decision + alternatives
  - target: context/telegram.md
    condition: how a proxy is consumed by the Telethon gateway
  - target: context/architecture.md
    condition: where the proxy repo/service/router sit in the layer model
last_updated: 2026-06-30
---

# Proxy

The fleet's proxies, organised as a **shared pool**: one proxy can carry several
accounts, and the pool is the *only* place proxies are stored or added. This
replaces the earlier per-account 1:1 proxy. Model + rationale: see
[decisions.md](./decisions.md) "Proxy pool".

## Language

**Proxy Pool**:
The fleet-wide collection of proxies shown on the Accounts page. Every proxy
exists here independently of any account, and accounts are drawn from it.
_Avoid_: proxy list, proxy bank.

**Proxy**:
One first-class entry in the pool — a `(type, host, port [, auth])` endpoint plus
its last connectivity-check result. Lives in the `proxies` table. Always a *pool*
proxy; there is no private/per-account proxy.
_Avoid_: account proxy, manual proxy (as a separate kind).

**Slot**:
One unit of a proxy's capacity. A proxy has `N` slots (global
`settings.proxy.max_accounts_per_proxy`, default 3); each assigned account fills
one. "2 / 3", "1 слот свободен", "Заполнен" are slot counts.
_Avoid_: seat, connection.

**Assignment**:
The link between an account and the one pool proxy it uses (`accounts.proxy_id`).
An account has **at most one** proxy; a proxy has **at most N** accounts.
Assignment is explicit (operator-driven), never auto-filled; an unassigned
account shows "—".
_Avoid_: binding, attach (use "assign").

**From-pool vs Manual** (account-edit modes):
*Из пула* assigns the account to an **existing** pool proxy that has a free slot.
*Вручную* **adds a new proxy to the pool and assigns** the account to it in one
step. Both end in the pool — *Вручную* is not a private proxy.
_Avoid_: custom proxy, inline proxy.

**Connectivity check** ("Проверить"):
The TCP-connect + geo/datacenter probe (`core/proxy_check.py`) run against a pool
proxy. Its result (status, exit IP, country, ASN, datacenter flag) is stored on
the proxy row and drives the card flag/status and the account row's
connectivity dot.
_Avoid_: ping, test.
