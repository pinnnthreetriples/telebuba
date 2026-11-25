---
name: conventions
description: Backend implementation and review rules.
last_updated: 2026-11-25
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

Tests copy a per-process template database (`tests/_db_template.py`); a test about building the database itself takes `@pytest.mark.fresh_database`, and `TELEBUBA_TEST_FRESH_DB=1` builds every one from scratch.
