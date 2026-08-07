---
name: grill-with-docs
description: Stress-test a significant Telebuba design against code and routed MEX memory, recording only durable outcomes.
when_to_use: >
  Invoke for explicit grilling/stress-testing requests or significant design
  decisions that cross project boundaries. Do not auto-invoke for routine edits.
---

# Grill With Project Memory

Interview the maintainer one decision at a time until the plan is precise. For every question, give a recommended answer. If code can answer it, inspect code instead of asking.

## Context

1. Read .mex/ROUTER.md and only the matching context route.
2. Use code/tests/manifests as the source of truth. Use a small mex timeline query only when prior rationale would change the decision.
3. Reuse the terminology already established by the loaded project context; surface contradictions instead of inventing parallel vocabulary.

## Grilling loop

- Resolve dependencies between decisions before moving deeper in the tree.
- Probe concrete edge cases, failure modes, ownership and persistence boundaries.
- Distinguish durable architecture constraints from temporary implementation detail.
- Ask one question at a time and wait for the maintainer when judgment is required.

## Memory discipline

Do not create CONTEXT.md, CONTEXT-MAP.md or docs/adr in this repository.

Update the matching .mex context only when a durable cross-task fact actually changed, and keep it inside the routed-memory size budget. Record a decision with mex log only when it is hard to reverse, surprising without rationale, and the result of a real trade-off. Closed bug narratives, PR status, measurements and transient state stay in Git/issues/tests instead of routed memory.
