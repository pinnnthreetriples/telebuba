---
name: proxy
description: Shared proxy-pool invariants.
triggers: [proxy, pool, socks5, exit ip]
edges:
  - target: context/telegram.md
    condition: connection routing
  - target: context/architecture.md
    condition: layer placement
last_updated: 2026-07-16
---

# Proxy
- `proxies` is the only proxy store; there are no private per-account proxy records.
- `accounts.proxy_id` assigns at most one proxy to an account; one proxy serves up to `settings.proxy.max_accounts_per_proxy` accounts.
- Assignment is explicit. “Manual” means create in the pool and assign in one operation.
- Checks persist connectivity, exit IP, geography, ASN, and datacenter status on the proxy row.
- API → proxy service → repository/check adapter; Telegram resolves credentials only inside `core/`.
- Deleting an assigned proxy detaches accounts after confirmation.