---
name: add-auth
description: Change authentication across core, service, API, and frontend layers.
triggers: [auth, login, jwt, session cookie, protected route]
edges:
  - target: context/architecture.md
    condition: layer placement
  - target: patterns/add-api-endpoint.md
    condition: protected endpoint
last_updated: 2026-07-16
---

# Add or Change Auth

## Ownership
- `core/auth.py`: hashing and JWT only.
- users repository/migrations: persistence.
- `services/auth/`: credential and session policy.
- `api/v1/auth.py` and `api/deps.py`: cookie transport and authorization dependency.
- frontend: login and protected-route behavior.

## Steps
Update schemas/config → core/repository → service tests → API/dependency tests → frontend/client → gates.

## Verify
JWT library stays in `core/auth.py`; cookie flags/TTL come from config; no public signup; invalid/missing session returns the shared 401 envelope; protected frontend routes handle 401; an empty `AUTH__SECRET` disables token issuance.