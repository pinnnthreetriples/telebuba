---
last_updated: 2026-08-04
---

# Change Auth
- `core/auth.py`: hashing/JWT only (argon2 + pyjwt live nowhere else); repository/migration: users.
- `services/auth/`: credential/session policy (`policy.py`) and the login rate limit (`_ratelimit.py`).
- `api/v1/auth.py` and `api/deps.py`: cookie transport and authorization. Only `login` / `logout` / `me` exist; `get_current_user` is mounted once per protected router in `api/v1/__init__.py`, not per route.
- Frontend: login and protected-route behavior.

Update schemas/config → core/repository → service tests → API/dependency tests → frontend/client → gates.

Verify: JWT library stays in `core/auth.py`; cookie flags/TTL are config-driven; no public signup; invalid sessions return the shared 401 envelope; empty `AUTH__SECRET` disables token issuance (login answers 503). Sessions are stateless — revocation is a per-user token-version bump (`revoke_sessions`), and every authenticated request re-issues the cookie for a sliding TTL, so both paths must keep working after a change.
