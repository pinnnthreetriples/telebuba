---
name: conventions
description: Backend implementation and review rules.
last_updated: 2026-10-10
edges:
  - target: context/architecture.md
    condition: layer boundaries, gateways or system design
  - target: patterns/INDEX.md
    condition: the change is a repeatable implementation task
  - target: patterns/add-api-endpoint.md
    condition: adding or changing an /api/v1 endpoint
  - target: patterns/add-service.md
    condition: adding business logic in a service
---

# Backend Rules

Backend coding rules live in `CODING_STANDARDS.md` at the repo root; read it for backend implementation or review. Fingerprint identity rules are in `context/runtime-telegram.md`.
