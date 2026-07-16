---
last_updated: 2026-07-16
---

# Change Auth
- `core/auth.py`: hashing/JWT only; repository/migration: users.
- `services/auth/`: credential/session policy.
- `api/v1/auth.py` and `api/deps.py`: cookie transport and authorization.
- Frontend: login and protected-route behavior.

Update schemas/config → core/repository → service tests → API/dependency tests → frontend/client → gates.

Verify: JWT library stays in `core/auth.py`; cookie flags/TTL are config-driven; no public signup; invalid sessions return the shared 401 envelope; empty `AUTH__SECRET` disables token issuance.
