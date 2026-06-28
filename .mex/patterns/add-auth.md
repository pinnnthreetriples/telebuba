---
name: add-auth
description: How authentication is structured across layers — JWT/hashing in core, policy in services/auth, cookie + Depends(get_current_user) in api, protected layout in the SPA.
triggers:
  - "auth"
  - "login"
  - "jwt"
  - "session cookie"
  - "get_current_user"
  - "protected route"
edges:
  - target: context/architecture.md
    condition: always — auth touches every layer
  - target: context/decisions.md
    condition: for the auth ADR (cookie/sliding-TTL, role-from-day-one, no signup)
  - target: patterns/add-api-endpoint.md
    condition: when protecting a new endpoint
last_updated: 2026-06-28
---

# Add / Touch Auth

Auth is the canonical cross-layer slice. The ADR (2026-06-28, `context/decisions.md`) locks:
**HttpOnly + Secure + SameSite session cookie, sliding TTL, no refresh rotation; `role` column
from day one (no RBAC until a 2nd role); no public signup (admin-seeded users).**

## Where each piece lives

- **`core/auth.py`** — password hashing (verify/hash) + JWT encode/decode. The **only** place
  the JWT library is imported and tokens are minted/verified.
- **`core/repositories/users.py`** + **migration** — the `users` table (`id`, `username`,
  `password_hash`, `role`, timestamps) and its queries. `role` exists from day one.
- **`services/auth/`** — policy: verify credentials → mint a session token; resolve a token →
  the current user; slide the session. Returns Pydantic models (`schemas/auth.py`). No FastAPI.
- **`api/v1/auth.py`** — `POST /login` (sets the cookie), `POST /logout` (clears it),
  `GET /me`. The cookie is set with `HttpOnly`, `Secure`, `SameSite`, and the sliding TTL.
- **`api/deps.py`** — `get_current_user`: read the cookie → `services.auth` → the user, or 401
  in the error envelope. Protected routes depend on it.
- **Frontend** — a protected-layout route gate (TanStack Router) that redirects to `/login`
  when `GET /me` 401s; the login form is a `pages/login` slice (TanStack Form + Zod).

## Steps

1. **Schema** — `schemas/auth.py`: `LoginRequest`, `UserRead` (incl. `role`), session model.
2. **Core** — `core/auth.py` (hash/JWT) + migration adding `users` + `core/repositories/users.py`.
   Config in `settings.auth` (secret, cookie name, TTL, cookie flags); secret from `.env`.
3. **Service** — `services/auth/` policy over core. Test it (`tests/services/test_auth.py`).
4. **API** — `api/v1/auth.py` routes + `get_current_user` in `api/deps.py`. Test login/logout/
   me + that a protected route 401s without the cookie (`tests/api/test_auth.py`).
5. **Seed** — an admin-seed path (CLI/migration/startup), since there is no public signup.
6. **Frontend** — protected layout + login page; regenerate the client.
7. **Gates** — backend pytest/ruff/ty/aislop + frontend gates.

## Gotchas

- The JWT lib must not be imported outside `core/auth.py` (one mint/verify point).
- Cookie flags are config, not literals — `settings.auth`.
- `role` is stored but **not branched on** until a second role exists (no premature RBAC).
- No signup endpoint — users are admin-seeded.
- Sliding TTL = re-issue the cookie on a valid request; do **not** add refresh-token rotation.

## Verify

- [ ] JWT/hashing only in `core/auth.py`; `users` repo + migration; `role` column present
- [ ] Policy in `services/auth/` (Pydantic, no FastAPI); cookie + `get_current_user` in `api/`
- [ ] Cookie is HttpOnly + Secure + SameSite, sliding TTL, no refresh rotation
- [ ] Protected route 401s (envelope) without a valid cookie — tested
- [ ] No public signup; admin-seed path exists
- [ ] Frontend protected layout redirects to /login on 401; client regenerated

## After

Run the GROW step from `ROUTER.md`.
</content>
