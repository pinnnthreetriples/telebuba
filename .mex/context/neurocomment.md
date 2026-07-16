---
name: neurocomment
description: Neurocomment runtime flow and invariants.
triggers: [neurocomment, campaign, listener, comment, solver]
edges:
  - target: context/services.md
    condition: domain logic
  - target: context/telegram.md
    condition: listener or posting action
  - target: context/warming.md
    condition: shared account readiness
last_updated: 2026-07-16
---

# Neurocomment
A persisted listener account watches active campaign channels. Each new post is handled in a tracked task; FastAPI lifespan reconciles the listener after restart.

## Pipeline
Map channel to campaign → filter post → select healthy under-quota account → win atomic `(channel, post_id)` claim → generate and deduplicate text → delay → post to the linked discussion → persist outcome.

## Challenge handling
Onboarding detects discussion restrictions and bot challenges separately. The configured solver supports text and image inputs, OpenAI or Gemini providers, cached decisions, retries, operator retry/skip actions, and channel backoff. Listener assignment remains persisted when runtime is stopped.

## Ownership
- `_runtime.py`, `_listener.py`: lifecycle and tracked tasks.
- `engine.py`, `_generate.py`, `_select.py`: post pipeline, account choice, quotas, dedup.
- `challenge.py`: challenge detection/decision/action flow.
- `board.py`, campaigns/readiness repositories: bulk-loaded UI state and durable claims.

## Invariants
- `handle_new_post` is listener-safe and must not leak exceptions.
- Atomic claims prevent duplicate comments across concurrency/restarts.
- Warming and listener roles are mutually exclusive.
- Telegram/provider access uses testable gateway seams.
- API/frontend contain no campaign execution policy; all limits are configuration-driven.