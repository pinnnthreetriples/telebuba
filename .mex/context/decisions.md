---
name: decisions
description: Key architectural and technical decisions with reasoning. Load when making design choices or understanding why something is built a certain way.
triggers:
  - "why do we"
  - "why is it"
  - "decision"
  - "alternative"
  - "we chose"
edges:
  - target: context/architecture.md
    condition: when a decision relates to system structure
  - target: context/stack.md
    condition: when a decision relates to technology choice
last_updated: 2026-07-01
---

# Decisions

## Decision Log

### Warming activity personas — operator-chosen cadence under the phase safety ceiling
**Date:** 2026-07-01
**Status:** Accepted (design, via grill; not yet built)
**Decision:** Warming gains a per-account **`activity_persona`** (`calm` / `normal` / `active`; RU labels **Спокойный / Обычный / Активный**), chosen in the start-warming modal beside `target_days` and persisted as a new column on `warming_account_state`. It establishes **two orthogonal layers**: **phase** (age+trust) stays the *safety ceiling* — per-phase daily action cap + `dm_min_age`/trust-band DM gates, unchanged — and **persona** is the operator's *target activity level*. Effective behaviour is always `min(persona, phase/trust)`, so a fresh account on `active` still cannot exceed the intro cap. Persona ≠ phase: "молодой аккаунт с активной персоной" is a valid state. The persona's primary lever is **sessions per day** (Спокойный 2–4 · Обычный 5–8 · Активный 10–14). The next-run interval is derived from the persona **instead of** the flat `cycle_sleep_min/max_hours`: `effective_sessions_today = min(persona-range draw, sessions the phase cap can afford ≈ daily_cap ÷ expected_actions_per_session)`, then those sessions are **spread evenly across the active window** (`gap = active_window ÷ effective_sessions`) with ±~25 % jitter and shifted into active hours — **no front-loading**; `flood_wait`/`quarantine` keep their own sleeps. Persona also sets per-session **reaction probability** (0.15 / 0.40 / 0.70) and **inter-account DM probability** (0.10 / 0.30 / 0.55, still behind the age+trust gate); **session size stays 1–3 channels** (persona varies *frequency*, not session bulk). The legacy age-based intensity ramp (`ramp_enabled`, `ramp_initial_*`, `ramp_full_age_hours`) is **retired**. **Story viewing** is added to the cycle for all personas — watch available stories of subscribed warming channels + dialogue peer accounts via a **new typed gateway action `WatchPeerStories`** (`stories.getPeerStories` on a non-self peer + `stories.readStories` to mark seen), counted as ~1 light action/session; **story posting is out of scope** for warming (operator posts manually via the profile modal). **Quiet hours are removed entirely** (config fields, `_gate_quiet_hours`, quarantine quiet handling, `WarmingSettingsSecret` fields, Settings UI) — the single "when awake" concept is **active hours** (08–23, account-local, unchanged); dead DB columns are left in place (no destructive migration). The **intro window is shortened 72 h → 24 h** (`_PHASE_HARD_FLOOR_AGE_HOURS` 72→24 and `_PHASE_DAY_BOUND["intro"]` 2→1); `dm_min_age_hours` stays 36 h deliberately (DM is the highest-risk action). Persona is set at start (change = stop/start, mirroring `target_days`); API/missing + new-start modal default = `normal`. No accounts are in warming at ship time, so there is no backfill concern.
**Reasoning:** The fixed 12–30 h sleep modelled "opens Telegram ~once a day" — for mature accounts it never approaches the safe daily budget (the 40/80 caps were dead) and it is a weak imitation of an engaged user, who returns several times a day. Making cadence an operator choice bounded by the same research-anchored phase caps fixes this **without weakening ban-safety** (the cap + age/trust gates still throttle young accounts). Keeping the session small and varying *frequency* is the natural-behaviour lever (many short visits, not one grind). Retiring the age-ramp removes a third scaler that would fight the persona — phase already encodes "young = quiet", so two clean layers (ceiling + level) are predictable. Removing quiet hours drops a redundant, confusing control (active hours already keeps activity out of the night). The 24 h intro is the operator's explicit speed/risk trade.
**Alternatives considered:**
- *Front-load sessions then park at the cap* — rejected by the operator; wanted an even spread across the whole day ("синхронно и профессионально").
- *Persona multiplies on top of the age-ramp* — rejected; three interacting scalers, unpredictable behaviour.
- *Persona as a global setting* — rejected; it is a per-account choice at start (like `target_days`), surfaced in the modal.
- *Auto-post stories for `active`* — rejected; needs a media/content pool + higher risk; manual posting stays in the profile modal.
- *Keep quiet hours as an optional daytime pause* — rejected; redundant with active hours, an extra confusing knob (YAGNI).
- *Editable persona mid-warming* — deferred; stop/start suffices (mirrors `target_days`).
**Consequences:** `schemas/warming.py` gains `activity_persona` on `StartWarmingRequest` + the state models and a persona `Literal`; `core/db.py` + a new migration add the `activity_persona` column on `warming_account_state`. `core/config.py` gains a persona-presets namespace (sessions/day, reaction prob, DM prob per persona) mirrored in `.env.example`, and **retires** `cycle_sleep_*` / `ramp_*` / `quiet_hours_*`. `services/warming`: `pacing.py`/`_transitions.py` next-run rewrite (persona→interval + even spread), `_cycle.py` reaction/DM probability from persona + a story-view step, `_loop.py` drops the quiet-hours gate, `pacing.py` phase table 72→24 + intro bound 2→1. New gateway action `WatchPeerStories` in `core/telegram_client/` (+ schema in `schemas/telegram_actions.py` and File-Map line). `settings_store.py` / `WarmingSettingsSecret` drop the quiet fields. Frontend: start-modal persona chips + «?»-tooltip + i18n (`ru`/`en`); the hey-api client is regenerated (gen-api drift gate). Tests via `/tdd` across services/api/frontend. Glossary: `activity_persona` / «персона активности» / «окно активности» captured in `context/warming.md`. Ships on its own branch/PR — **not** the design-handoff PR #192.

### Proxy pool — one shared first-class proxy serving ≤N accounts (single source of truth)
**Date:** 2026-06-30
**Status:** Accepted (design, via grill; not yet built)
**Decision:** The design's **Прокси-пул** replaces the per-account 1:1 proxy with a **shared pool**. A new first-class **`proxies`** table is the *only* proxy store ("все прокси добавляются ТОЛЬКО через пул") — columns: `id` (pk), `proxy_type` (`socks5`/`https`), `host`, `port`, `username`, `password`, the connectivity-check results (`status`, `last_checked_at`, `last_error`, `exit_ip`, `country_code`, `country_name`, `asn`, `is_datacenter`), `created_at`, `updated_at`; identity unique on `(host, port, proxy_type)` so the pool never holds duplicates. **`accounts.proxy_id`** is a nullable FK → `proxies.id` (`ON DELETE SET NULL`): an account references **at most one** proxy (account-side cardinality ≤1 — the operator's "1:1 оставь"), and a proxy serves **up to N** accounts. **Capacity N** = global `settings.proxy.max_accounts_per_proxy` (default **3**), enforced on assignment (cannot attach to a full proxy); no per-proxy override. The existing **`_account_proxies`** (1:1, keyed by `account_id`) is **migrated** into `proxies` + `accounts.proxy_id` — each per-account proxy becomes a pool proxy, identical `(host,port,type)` across accounts collapse into one shared proxy, then `_account_proxies` is dropped; legacy groups that already exceed N stay over-capacity (a migration never detaches an account) while new assignments past N are blocked. The Telethon seam **`fetch_account_proxy_settings(account_id)`** is rewritten to resolve `account.proxy_id → proxies` row, so connection routing (`core/telegram_client/_client.py`) is untouched. **Account-edit** keeps the design's two modes: *Из пула* = assign to an existing pool proxy that has a free slot; *Вручную* = add a new proxy to the pool **and** assign this account in one step (still lands in the pool — no private/parallel store). The Accounts-page pool card *+ Добавить* adds an (optionally unassigned) pool proxy. Connectivity/geo check (`core/proxy_check.py`, already decoupled from the table) runs per pool-proxy row and powers the card flag/status + the account row's connectivity dot/flag/type. Deleting a pool proxy detaches its accounts (FK SET NULL), confirmed when in use. Assignment is explicit — **no auto-fill** on account creation (the design shows unassigned "—"). A new **`api/v1/proxies.py`** router exposes the pool (the per-account proxy service was never surfaced after the split-stack migration).
**Reasoning:** The operator's constraint — every proxy lives in the pool, "professionally, без костылей" — rules out keeping `_account_proxies` beside a pool table (two parallel proxy stores + duplicated check/geo logic = the crutch to avoid). One `proxies` table with an account→proxy FK is the minimal faithful model: it preserves account-side 1:1, makes the pool the single source of truth, reuses the connectivity check verbatim, and leaves the Telethon gateway unchanged because the seam resolves the FK. Global capacity (not per-proxy) is YAGNI-correct — proxy plans rarely differ per host in this fleet, and a config knob is one line.
**Alternatives considered:**
- *Keep `_account_proxies` for "Вручную" + a separate pool table* — rejected by the operator ("все прокси только через пул"); two stores duplicate the proxy config + check/geo code.
- *"Вручную" = a private proxy outside the pool* — rejected for the same reason; *Вручную* now means add-to-pool-and-assign.
- *Per-proxy capacity column* — rejected YAGNI; one global default of 3.
- *Auto-assign accounts to a least-loaded proxy on creation* — rejected; the design shows explicit "—" (unassigned) and an operator-driven assignment.
**Consequences:** New migration creates `proxies` + `accounts.proxy_id`, backfills from `_account_proxies`, drops `_account_proxies`. `schemas/proxy.py` reworked around a pool `Proxy`/`ProxyRead`/`ProxyCreate` + pool-usage view (replacing the `AccountProxy*` family); `core/repositories/_proxies.py` → a pool repo keyed by `proxy_id` with assignment + capacity helpers; `services/accounts/proxy.py` becomes a pool service. `AccountRead`'s `proxy_*` fields are sourced via the FK join (or folded into a nested `proxy` object). New `api/v1/proxies.py` (+ `Depends(get_current_user)`); the generated TS client is regenerated (gen-api drift gate). Frontend: the `PROXY_POOL` mock in `pages/accounts` + `ProxyAddModal`/`ProxyForm` + the AccountEdit *Из пула/Вручную* modes are wired to `/api/v1/proxies`; the accounts-table proxy column de-mocks `ptype`/connectivity. `settings.proxy.max_accounts_per_proxy` added + mirrored in `.env.example`. File Map gains the pool repo/router. Glossary: see `context/proxy.md`.

### Frontend/backend split — React SPA over a FastAPI JSON API (supersedes "Use NiceGUI")
**Date:** 2026-06-28
**Status:** Accepted (design, via grill; not yet built)
**Decision:** NiceGUI is removed. The UI becomes a standalone **React + TypeScript (strict) + Vite** SPA; the Python app exposes a thin **FastAPI + uvicorn** JSON API under `/api/v1` over the existing `services/`. A new top layer **`api/`** replaces `features/` as the UI-thin layer — it validates input, calls a service, serializes the result. `api/` may import **only** `services/`, `schemas/`, `core.config`, `core.logging`, `fastapi` — never `core.db`/`core.repositories`/`core.telegram_client`/`sqlalchemy`/`telethon` (the `features→{config,logging}` firewall moves to `api→{config,logging}+services+schemas+fastapi`). **Repo layout:** Python stays at repo root; the SPA lives in a sibling `frontend/`. **Runtimes** (warming, neurocomment) move from NiceGUI `on_startup`/`on_shutdown` to FastAPI **`lifespan`**; **uvicorn runs single-worker** (the runtimes are in-process asyncio tasks — multi-worker would duplicate Telegram work and race the DB). **Prod serving:** `frontend/dist` via FastAPI `StaticFiles` + a catch-all returning `index.html`; **dev** = uvicorn + vite with a `/api` proxy. **Auth (near-term):** `users` table + migration + password hashing + JWT encode/decode in `core/`; auth policy in `services/auth/`; routes + `Depends(get_current_user)` + **HttpOnly+Secure+SameSite session cookie (sliding TTL, no refresh rotation)** in `api/`; `role` column from day one (no RBAC until a second role); no public signup (admin-seeded). **Cross-cutting contract:** error envelope `{error:{code,message,fields?}}` (422 remapped into it) + generic `Page[T]={items,next_cursor}` cursor pagination. **Frontend stack:** TanStack Router/Query/Table/Form, Tailwind + shadcn/ui (Radix), `@hey-api/openapi-ts` client generated from OpenAPI (CI drift-check), Sentry React, Vitest+RTL, self-hosted Inter + flag-icons (zero CDN).
**Reasoning:** The hand-built NiceGUI re-implementation could not match the design, and NiceGUI no longer renders any UI — it had decayed to a thin wrapper over the FastAPI/Starlette/uvicorn it bundles while dragging an unused Vue/Quasar/websocket runtime. A real React SPA over a typed JSON API gives pixel-fidelity to the design (which is React+CSS underneath), end-to-end type safety (pydantic → OpenAPI → TS), and a maintainable/hireable frontend. Holding `api/` to the same gateway discipline `features/` had (data only via services) preserves the executable arch firewall and keeps services UI-agnostic/reusable (tests, scripts, SSE). Python-at-root avoids a pointless mass move of every import/config. Single-worker is mandatory because the runtimes assume one process.
**Alternatives considered:**
- *Keep / re-implement UI in NiceGUI* — rejected: cannot reach design fidelity (drift already burned us); pays for an unused UI framework.
- *`api/` reads `core/repositories` directly for simple GETs* — rejected: exactly the drift the firewall caught when `features/neurocomment/_page.py` imported `core.db`; thin pass-through services are the accepted cost.
- *Move Python under `backend/` for symmetry* — rejected: churns every import/tool-config/File-Map for cosmetics.
- *uvicorn multi-worker for throughput* — rejected: duplicates the single-process runtimes; extract runtimes to a separate process first if ever needed.
- *Access+refresh token rotation* — rejected: YAGNI for an internal single-tenant tool; sliding HttpOnly cookie is simpler and XSS-safe.
**Consequences:** Supersedes *Use NiceGUI instead of a split frontend/backend*. New `api/` package + `frontend/` tree; `main.py` becomes the FastAPI/uvicorn composition root (lifespan + static mount). **`tests/test_architecture.py` must re-point the firewall (`features`→`api`) in lockstep with introducing `api/`**, or it encodes stale law. Coverage source becomes `core/schemas/services/`**`api`** (drop `features`). New config namespaces `settings.api` + `settings.auth`; `settings.ui` (incl. `reconnect_timeout`) retired; `uvicorn.run(app)` imported in `main.py` so deptry sees it. The whole frontend is governed by a new `context/frontend.md` (see the FSD ADR), not the Python non-negotiables. Docs to rewrite: `architecture.md`, `conventions.md` (#1/#5/#11; scope #3/#10 to backend), `AGENTS.md` (Stack/File-Map/non-negotiables), `ci.md` (frontend jobs + gen-api drift), `logging.md` (Logs page → React), `stack.md`, `patterns/add-feature.md` (→ `add-api-endpoint` + `add-frontend-slice` + `add-auth`). Glossary: "feature"/"page" now mean the FSD frontend concepts; the backend speaks of "api endpoints" + "services".

### Localization lives in the frontend — the API is locale-neutral
**Date:** 2026-06-28
**Status:** Accepted (design, via grill; not yet built)
**Decision:** The API returns locale-neutral data only — stable status **codes/enums** and **ISO-8601 timestamps** — never pre-translated display text. All user-facing strings and all relative-time/number/date formatting live in the frontend via **react-i18next** (ru default, en next) + `Intl`. The service-side "single-source RU localization" approach (services emitting RU labels like `_account_status_label`, `_CHANNEL_STATUS_RU`, RU relative-time) is **reversed**: those maps move into frontend locale resources (`ru.json`/`en.json`). Log events should carry a stable event **code + structured params** (FE localizes) — staged refinement, not day-one. i18n is frontend-only; no server-side `Accept-Language`.
**Reasoning:** English-next is impossible if the server bakes RU into responses. A locale-neutral API (codes + ISO) is the only design that supports N languages without re-touching every endpoint — the explicit "avoid future refactors" goal. It also sharpens the contract: status fields become enums on the wire (already true for `AccountStatus`), and the FE owns presentation.
**Alternatives considered:**
- *Server-side i18n via `Accept-Language`* — rejected: pushes presentation into the API, duplicates locale data across layers, and the SPA still needs its own i18n for static chrome (two sources of truth).
- *Keep RU-in-services, add EN in services later* — rejected: every label/relative-time becomes a per-language server branch — the refactor we are avoiding.
**Consequences:** `schemas/` expose enums/literals (codes), not RU strings; services stop computing display labels and relative time. The ruff `RUF001-003` "Cyrillic intentional" ignores largely leave the Python side (UI strings move to FE locale JSON). `/api/v1/logs` and status read-models return codes; the React Logs/board localize them. Gemini-generated comment text is unaffected (its language is a prompt concern, not UI i18n).

### Frontend architecture = Feature-Sliced Design, enforced in CI
**Date:** 2026-06-28
**Status:** Accepted (design, via grill; not yet built)
**Decision:** The frontend uses full **FSD layers** — `app / routes / pages / widgets / features / entities / shared` — with strictly **one-directional imports** (a layer imports only lower layers; no sideways imports within a layer) and **each slice exposing a public API via `index.ts`** (internals private). Data access is **only through `shared/api`** (the generated hey-api client + TanStack Query); components never fetch directly. Design **tokens live in `tailwind.config`** (extracted from the design file *before* `web/` is deleted) as the single source of truth. Enforced by **Steiger** (or `eslint-plugin-boundaries`) + ESLint + Prettier + `tsc --strict` + Vitest in CI; **Vitest coverage floor 80%** (generated/shadcn/pure-presentational excluded). Mirrors the backend's feature/layer isolation — one mental model for the whole repo.
**Reasoning:** Structure-now over refactor-later (explicit user choice). FSD is the documented methodology that scales and maps 1:1 onto the existing backend isolation rules. The **boundary linter is the load-bearing part** — without an executable check, layered conventions erode (the backend learned this: rule #1 didn't hold until `test_architecture.py` made it executable). 80% UI coverage is the honest floor: 90% branch on presentational components is high-cost/low-signal; logic (hooks/features/api) is tested strictly.
**Alternatives considered:**
- *Type-based folders (components/hooks/utils)* — rejected: doesn't scale, becomes a dumping ground.
- *Minimal `pages`+`shared` only* — rejected: user wants the full structure to avoid later re-homing; FSD layers exist as a skeleton from day one, files added as screens land.
- *Atomic Design* — rejected: a component taxonomy, not an app architecture; dated for app structure.
- *90% FE coverage parity* — rejected: low signal on UI; 80% + exclusions is the professional floor.
**Consequences:** New `context/frontend.md` documents the FSD layers + import matrix + slice public-API rule + the FE gate set; `ROUTER.md` routes to it. CI gains frontend jobs (eslint/prettier/tsc/vitest/boundary-lint) + the gen-api drift check. "feature"/"page" become FSD terms in the glossary.

### Neurocomment captcha — proactive challenge solver on onboarding (Ф2, #120)
**Date:** 2026-06-24
**Status:** Accepted (design, via grill; not yet built)
**Decision:** The Ф1 lazy-on-comment-failure captcha model is replaced by a **proactive solver** that runs inside `_onboard_pair` immediately after `JoinDiscussionGroup`. A new typed read-action `WaitForBotChallenge(chat_id, timeout_s)` in `core/telegram_client/_read.py` opens a short-lived `events.NewMessage` subscription on the joined discussion group, returning the first `BotChallengeMessage` matching the predicate (sender bot ∧ `ReplyInlineMarkup` ∧ adressed-to-us via mention OR `tg://user?id=…` OR reply-to-join service message) or `None` on timeout. A new service module `services/neurocomment/challenge.py` orchestrates: image early-exit → audit-cache lookup by `challenge_hash = sha256(normalize(text) | sorted button labels)` → Gemini call with `responseSchema`-enforced `ChallengeDecision` (`action ∈ {click_button, send_text, give_up}` + `button_index` + `text` + `confidence` + `reasoning`) → log-normal humanization pause → `ClickButton` / `send_message` → row persisted `outcome='pending'`. The engine resolves `pending` to `solved` / `failed` on the first comment attempt for `(account, channel)`. Migration #14 adds the `neurocomment_challenges` audit-and-cache table (one source, two roles: audit + global cache by hash) plus `neurocomment_campaigns.solver_enabled BOOLEAN DEFAULT NULL` for per-campaign override of the global `settings.neurocomment.challenge_solver_enabled` (default **`False`**, opt-in roll-out, mirrors #132's pattern). Failure escalation: one-shot per `(account, channel)` (no cascade — natural pool redundancy), K-failure channel back-off cloning the #131 deletion-back-off shape in `_state.py`. Vision (image-captcha) deliberately deferred to Phase 2 — image-challenge counter is the data signal for "build vision or blacklist".
**Reasoning:** Guardian bots auto-delete their prompt after the answer window (typically 60–120 s); a lazy post-mortem look-up at comment time would find nothing in the majority of cases, and a stale callback returns `BUTTON_DATA_INVALID`. Lazy is *physically* dysfunctional once we have a solver. Proactive on onboarding pays the wait **once per `(account, channel)`** (not per-comment), parallelised across the campaign batch — cheap. `WaitForBotChallenge` keeps the gateway layered (non-negotiable #6): the predicate, the wait, the typed return value all live inside `core/telegram_client/`; the orchestration (cache, Gemini, humanize, click, persist) lives in `services/neurocomment/challenge.py`. `responseSchema`-enforced output makes parse-fails effectively impossible — server-side validation by Google removes a class of runtime retries on our side. The audit-and-cache merge into one table avoids dual-write rasync risk; the cache is just a `SELECT … outcome='solved'` projection. The opt-in feature flag matches our own paste-the-roll-out pattern (#132 semantic dedup landed with `threshold=0` default = off) — a new behaviour that costs Gemini tokens and clicks buttons in live chats does not auto-activate.
**Alternatives considered:**
- *Lazy on comment failure (Ф1 carry-over)* — rejected: prompt is gone by then in ≥ 90% of cases; stale callbacks fail; we cannot click a missing message.
- *Hybrid (proactive on onboarding + lazy retry on later comment failure)* — rejected: doubles the surface for negligible gain; the "later comment failure" almost never means a still-solvable challenge (it usually means `chat_restricted`).
- *Polling `get_messages` instead of a typed wait-action* — rejected: introduces magic numbers (sleep interval, poll count) and a guaranteed-to-miss race when Shieldy deletes the prompt between polls.
- *Piggyback on the existing listener account* — rejected: listener is a different account; it can see the bot message but cannot click for us (callback bound to clicker); pure complexity for no win.
- *Cascade to the next pool account on solver failure* — rejected: burns the pool on a hard channel; the natural per-account onboarding already gives independent redundancy.
- *Gemini JSON-mode + local Pydantic validation* — rejected: weaker than `responseSchema` server-side; we'd add retry logic for malformed JSON.
- *Vision in Phase 1* — rejected: spends design+test effort on a sub-problem whose prevalence in our channels is unknown; image early-exit + audit counter gives the data first.
**Consequences:** New files: `schemas/challenge.py` (`BotChallengeMessage`, `ChallengeDecision`, no behaviour), `services/neurocomment/challenge.py` (~150 lines, solver orchestration), `core/repositories/neurocomment/_challenges.py` (audit+cache queries). Extended: `core/telegram_client/_read.py` (+`WaitForBotChallenge` action+dispatcher), `core/gemini.py` (+optional `response_schema_json` on `GeminiRequest`), `core/migrations.py` (#14), `services/neurocomment/onboarding.py` (~10 lines: call solver after a successful join), `services/neurocomment/_state.py` (+per-channel `challenge_failed` counter cloning the #131 cooldown shape), `core/config.py` + `.env.example` (9 new knobs). UI: `features/neurocomment/_page.py` gains the split-aware status badges, four header counters with time-window toggle, drill-down panel with raw text + Gemini `reasoning` + Retry/Skip actions per pair, and a per-campaign solver-override switch (driven by `neurocomment_campaigns.solver_enabled`). The Ф1 `_GATE_ERRORS` block in `onboarding.py` is rebadged from generic `captcha_gated` to `chat_restricted` (see the separate state-split decision); solver-resolved pairs land in `bot_challenge` / `ready`. The `confidence` field on `ChallengeDecision` is recorded from day one but used only in Phase 2 (human-queue routing). The drill-down UI is the in-place stand-in for a dedicated human-queue page in Phase 1 — operators read `reasoning`, decide, act on the same row, no new page needed yet.

### Neurocomment state model — `chat_restricted` ≠ `bot_challenge` (Ф2, #120 prerequisite)
**Date:** 2026-06-24
**Status:** Accepted (design, via grill; not yet built)
**Decision:** The Ф1 single per-`(account, channel)` state `captcha_gated` is split into two unrelated states + one derived channel state: **`chat_restricted`** (Telegram-level write block — `ChatWriteForbiddenError` / `ChatGuestSendForbiddenError` / `UserBannedInChannelError`; not solvable; 24 h retry then possible permanent skip); **`bot_challenge`** (third-party guardian-bot inline-button challenge; solvable by the `challenge_solver`); **`bot_challenge_backoff`** (channel-level derived: K consecutive solver failures on the same channel within a window → escalating in-memory cooldown, mirrors #131 deletion back-off, same `_state.py`, separate counter). The board read-model status enum changes accordingly: `captcha_gated` is removed, replaced by `chat_restricted` + `bot_challenge` + `bot_challenge_backoff`. The code identifier `captcha` is retired from new code in favour of `challenge` (+ `challenge_solver`); the noun "captcha" survives only in user-facing copy and ADR titles.
**Reasoning:** The two failure modes need **opposite** system responses. `chat_restricted` should never invoke the solver (clicks won't help — it's the chat's setting or the account's status); `bot_challenge` is exactly what the solver was built for. Conflating them under `captcha_gated` would silently waste Gemini tokens on unsolvable channels, lose the signal to escalate channel-level back-off correctly, and force the UI to render misleading "captcha" labels for Telegram-level bans. Splitting state at the data layer pays one migration column (and three new badges) once, then permanently rules out a class of bugs. The terminology rename (`captcha` → `challenge`) aligns the glossary with what the code actually does: there is no "captcha" in MTProto — the artifact is a bot-emitted inline-keyboard challenge.
**Alternatives considered:**
- *Keep single `captcha_gated`, branch inside the solver* — rejected: the branch lives at runtime, so every reader of state + every UI surface still sees the conflated label; the solver is invoked on `chat_restricted` for nothing.
- *Solver-side runtime classification* (`ChatWriteForbiddenError` → flag "skip solver" inside the same state) — rejected: leaks core/Telethon error types into the service-level state machine and across the UI boundary; the same anti-pattern would re-leak on every adjacent feature.
- *Three independent enums instead of one* — rejected: the channel-state derivation is naturally a *projection* of pair-level state + counters, not an independent column; one source of truth is simpler.
**Consequences:** Migration **#14** carries the data move alongside the audit-and-cache table (one migration, two changes). `core/repositories/neurocomment/_readiness.py` updates the readiness writer to set the new state values. `services/neurocomment/onboarding.py` switches `_classify_join` to map `_GATE_ERRORS` → `chat_restricted` (was `captcha_gated`) and adds the solver call → `bot_challenge` / `ready`. `services/neurocomment/board.py` extends `_CHANNEL_STATUS_RU` and `_CHANNEL_STATUS_ICON` (already a dict, easy diff). `features/neurocomment/_page.py` renders three badges in place of the old one. `engine._select_account` already filters by `ready=True`, so it transparently inherits the split. **Backwards-compat note:** existing pre-Ф2 rows with `captcha_gated` are migrated to `chat_restricted` (the conservative remap — solver will not run on them; an operator can re-onboard a pair if they suspect it was actually a `bot_challenge`).

### Neurocomment — separate event-driven package, dedicated listener, campaign model
**Date:** 2026-06-22
**Status:** Accepted (design, via grill; not yet built)
**Decision:** The neurocomment feature (auto-commenting in channels' linked discussion groups) is a **separate `services/neurocomment/` package**, not an action-type inside the warming runtime. Its runtime is **event-driven**: a dedicated *listener* account subscribes to channel posts via a new gateway-owned `NewMessage` listener that surfaces a typed `NewPostEvent` (no Telethon leaks); warmed-pool accounts do the commenting. Work is organized into **campaigns** (`campaign_id`) binding {channels, accounts, prompt — the product mention is written into the prompt itself}. A **channel belongs to exactly one active campaign**; an **account may serve many campaigns** but its daily action budget is **per-account**. Accounts **pre-join** discussion groups and solve captcha at **campaign onboarding**, so a fresh post is commented immediately. New typed actions (`CommentOnPost` via Telethon `comment_to`, `ClickButton`, `GetLinkedDiscussionGroup`) follow the executor pattern.
**Reasoning:** Warming is *periodic* (sleep-until-next-run); neurocomment must be *real-time* (be among the first commenters) — a fundamentally different trigger model. `context/warming.md` scopes its runtime to warming and says add a new model when semantics differ. Feature isolation forbids polluting warming's cycle. A dedicated listener gives a single detection source (no duplicate detection across the fleet) and keeps the low-risk listen role off commenting accounts. Pre-join at onboarding is required for "be first" — join+captcha latency on the hot path would lose the race.
**Alternatives considered:**
- *Action-type inside warming runtime* (rejected — mixes event-driven with the periodic sleep-loop, pollutes the warming domain).
- *Every pool account listens* (rejected — duplicate detection, mixes listen/act roles, fleet-synchrony signal).
- *Lazy join+captcha at first post* (rejected — slow first comment, captcha latency on the hot path).
**Consequences:** New `services/neurocomment/` mirrors warming's *patterns* (per-account asyncio.Task ownership, pacing/readiness, CAS state, board, seams), not its code; reuses `trust`/`spam_status`/`evaluate_readiness` and warming anti-ban primitives. A new gateway **listener** capability (push) is added alongside the existing pull `execute`. State model is event-driven (`idle → commenting → cooldown` + `flood_wait`/`quarantine`/`error`; listener `listening`/`error`) — no `sleeping`-until-next-run. Daily limit is the account's own (warming → graduate-to-pool → comment are sequential phases, so no shared counter). New tables keyed by `campaign_id`. A deterministic product-mention ratio (%) plus a "K organic comments first" gate were considered and cut (YAGNI, per user): the product instruction and anti-detection wording live in the campaign prompt. Revisit a code-enforced ratio only if a guaranteed promo fraction is needed (a prompt cannot reliably hold a frequency across independent calls). Quality gate is lightweight (prompt-driven) first; split LLM-judge + semantic dedup, image-captcha (Gemini vision), and comment-deletion back-off are deferred to later phases. Supersedes the `services/comments/` placeholder name in `state/active.md`.
**Ф0 update (2026-06-22):** spike #113 PROVED `comment_to` end-to-end (real outsider account: resolve channel → resolve linked group → real join → comment posted). Gate passed → Ф1 (#114–#119) unblocked. Captcha-SOLVING was NOT proven (test channel had no entry captcha) → Ф1 onboarding (#117) reduced to **detect-and-skip**; actual captcha-solving deferred to spike #120 (Ф2). Reconfirmed: `comment_to` requires group membership → pre-join at onboarding stays mandatory.
**Ф2 anti-detect update (2026-06-23, specced via grill — not yet built):** the deferred "comment-deletion back-off" and "semantic dedup" are now resolved.
- **Deletion detection = poll re-read**, NOT `events.MessageDeleted`. Telethon's deletion event is doc-confirmed unreliable and only carries `chat_id` for channels/supergroups *if* the receiving account is inside that group's update stream — the listener only watches channel broadcasts. `get_messages(ids=[…])`→`None` is the deterministic, batchable check. It runs as one **periodic asyncio sweep**, so the runtime becomes **event-driven + a periodic deletion sweep** (still a plain interval, not cron/APScheduler — does not reopen that decision).
- **Back-off = escalating in-memory channel cooldown** — mirrors the per-account `_state` cooldown but keyed by channel; recomputed each sweep (self-healing on restart), no migration. A WARNING surfaces on the existing errors feed.
- **Semantic dedup = local token-set Jaccard**, NOT Gemini embeddings — an embedding call on the first-commenter hot path is the wrong latency trade; group+window scoped, threshold `0` disables it. Exact-hash `try_reserve_sent` stays the atomic claim.
- New gateway read action `CheckMessagesAlive`; all knobs in `settings.neurocomment`.

### Neurocomment account selection — bulk signal load + cached spam on the post path
**Date:** 2026-06-23
**Status:** Implemented
**Decision:** `engine._select_account` loads every selection signal in **one bulk pass** (accounts, per-channel readiness, warming state, cached spam, device fingerprints, and two `GROUP BY account_id` quota counts), then scores candidates purely in memory (mirrors `services/neurocomment/board`). Spam on the post path is read **from cache only** (`list_spam_statuses`); @SpamBot is **never re-probed during selection**. A one-shot spam probe is moved to **campaign onboarding** (`_probe_account_spam`) so a serving account always carries a verdict.
**Reasoning:** The old path ran ~7 DB reads + a spam refresh + a trust read **per candidate** — O(N) per post — and a stale-cache candidate triggered a live @SpamBot probe on the hot path. Probing @SpamBot per post is itself a ban signal (the `refresh_spam_status` docstring says so). Bulk-loading makes selection cost flat in fleet size (the stated growth goal); cached-only spam removes the hot-path network call; onboarding owns freshness. `evaluate_readiness` blocks only on `spam == "limited"` and trust ignores `"unknown"`, so cached-only never *newly* blocks an account — it only stops re-probing.
**Alternatives considered:**
- *Keep per-account `refresh_spam_status` on selection* (rejected — doesn't scale, re-probes @SpamBot on the hot path).
- *Hybrid: bulk DB + refresh only the chosen candidate's stale spam* (rejected — reintroduces hot-path network for the common case; complexity for no anti-ban win).
**Consequences:** New `core/repositories/neurocomment/_quota.py` holds the per-account + bulk grouped count readers (extracted from `_comments.py` once it crossed the aislop 400-line budget). Migration **#13** adds three composite indexes (`account_id,status,created_at` / `channel,account_id,status,created_at` / `campaign_id,channel,status,created_at`) sized to the bulk query shapes — EXPLAIN QUERY PLAN confirms all three are used (two covering). `_recent_channel_comments` now uses a channel-scoped SQL read instead of load-all-and-filter. The per-account count readers stay as public API + the parity-test oracle. Story-image normalization is offloaded via `asyncio.to_thread` (was blocking the NiceGUI loop). **Deferred (`# ponytail`):** the select→claim micro-race (a per-account lock would close it); unbounded per-post tasks (semaphore if volume grows).

### Architecture enforcement — features→core allowlist as an executable firewall
**Date:** 2026-06-23
**Status:** Implemented
**Decision:** The "UI-thin" rule (#1) + gateway rule (#6) are now an **executable test**, not prose: `tests/test_architecture.py` enforces that `features/**` may import from `core` **only** `core.config` + `core.logging`; every other core module (db, repositories, telegram_client, gemini) fails the build. The firewall also runs as a **pre-push hook** (`arch-guard`).
**Reasoning:** Documentation is a "wish" for an agent; only a failing check constrains it. The prior arch test banned only `sqlalchemy`/`telethon` from features, so `features/neurocomment/_page.py` had drifted to importing `core.db` (9 functions) + a repository — a #1/#6 violation prose didn't hold. An allowlist (config+logging) is more future-proof than a denylist: it also blocks the next temptation (`core.telegram_client`, `core.gemini`).
**Alternatives considered:**
- *`import-linter` or a bespoke `tools/arch_guard.py` + YAML DSL* (rejected — the repo's AST-based `test_architecture.py` already does this; a second tool/DSL is duplication).
- *Denylist `{core.db, core.repositories}`* (rejected — misses future core modules).
- *GitHub branch-protection required checks* (unavailable — private repo on the free plan; the pre-push hook + red CI are the teeth instead; GitHub Pro would enable hard merge-blocking).
- *PR checklist / CODEOWNERS* (rejected for the agent problem — both gate human review, not the agent, and are theater on a solo repo).
**Consequences:** `_page.py`'s direct `core.db`/repository use moved behind a new `services/neurocomment/campaigns.py`; its `link_channel` converts the repository's `ChannelAlreadyAssignedError` into a typed `ChannelLinkOutcome` so the exception never crosses into the UI (#2). The account list comes via the existing `services.accounts` re-export. The campaign-setup service functions are thin pass-throughs — the accepted cost of the layer rule.

### Warming audit — scheduling, resilience, and UX fixes
**Date:** 2026-06-20
**Status:** Implemented
**Decision:** Fixed 11 issues in the warming runtime: scheduling/ban-risk, board indicators, cycle resilience, RU localization, channel feedback, reconcile aborting quarantine recovery, cap=1 lockout, trust-ceiling progress, one-sided faded-pair DMs, case-sensitive invite dedup, stale doc.
**Files changed:** `services/warming/_loop.py`, `_runtime.py`, `_chat.py`.
**Result:** 536 tests green, coverage ≥ 90%.

### Accounts QA audit — 15 fixes, single-source localization
**Date:** 2026-06-20
**Status:** Implemented (PR #104)
**Decision:** Single-source RU localization: service emits raw `AccountStatus` + RU relative time; `_account_status_label` is the only translation point. UI sends `username=None` when unchanged to avoid `USERNAME_NOT_MODIFIED`. Error snapshots not cached. Footer hides only on success. Story kind inferred from extension. Delete errors translated. Search debounce added. Uploader CSS scoped to `.tb-profile-dialog`.
**Files changed:** `features/accounts/_table.py`, `services/accounts/_table.py`.
**Result:** 543 tests green, coverage ≥ 90%.

### Introduce `services/` layer between `features/` and `core/`
**Date:** 2026-06-10
**Status:** Active
**Decision:** All business logic lives in `services/<domain>/` or `services/<domain>.py`. `features/` stays UI-thin (NiceGUI page/components + handlers that call services). `core/` stays infrastructure-only.
**Reasoning:** Without `services/`, business logic ends up in UI handlers and cannot be reused safely from runtime tasks, scripts, or another feature without cross-feature imports. With `services/`, the same code path is reused without UI coupling.
**Alternatives considered:**
- *Logic in `features/`* (rejected — duplication and feature-boundary violations).
- *Logic in `core/`* (rejected — core is for infrastructure adapters, not domain rules).
- *Single-file services layer* (rejected — god-module risk).
**Consequences:** `services/` is the home of every algorithm/state transition/domain operation. Tests target services directly with mocked `core/` adapters. Layer matrix in architecture.md has four layers.

### Typed Telegram actions + central executor
**Date:** 2026-06-10
**Status:** Active
**Decision:** Every Telegram action is a Pydantic class in `schemas/telegram_actions.py` (`JoinChannel`, `PostComment`, `UpdateProfile`, ...). Services and features call `core.telegram_client.execute(account_id, action)`; the executor pattern-matches on the action model and dispatches inside the core gateway.
**Reasoning:** Direct SDK calls scattered across services make mocking painful and bypass the central point for lifecycle/error/logging policy. Typed actions give validation at the boundary, audit-friendly logs, and testability without touching Telethon outside `core/`.
**Alternatives considered:**
- *Direct Telethon calls from services* (rejected — bypasses the gateway).
- *String-based action registry* (rejected — loses type safety).
**Consequences:** `schemas/telegram_actions.py` owns action schemas. `core/telegram_client/` exposes one public executor. Services compose actions; they do not orchestrate raw SDK calls.

### Outbox pattern for non-trivial Telegram actions
**Date:** 2026-06-10
**Status:** Superseded 2026-06-14 (never built) → direct executor with per-cycle persisted state.
**Original decision:** Services persist Telegram intents in a `telegram_outbox` SQLite table. A worker picks them up and calls `execute`.
**Why superseded:** The outbox was never implemented, and the shipped design is single-process. Runtime progress is persisted on `warming_account_state`; `reconcile_warming_runtime()` rebuilds in-memory tasks on restart. A separate intent table + worker is premature until execution becomes multi-process.
**Replacement decision:** Services call `core.telegram_client.execute(account_id, action)` directly. Durability is per-cycle state, not a queue. Reopen an outbox/queue only when execution goes multi-process.
**Consequences:** No `telegram_outbox` table and no `telegram_outbox` module in `services/`.

### Config namespaces — nested Pydantic settings instead of one flat blob
**Date:** 2026-06-10
**Status:** Active
**Decision:** `core/config.py` exposes a `Settings` with nested namespaces: `settings.warming`, `settings.gemini`, `settings.telegram`, `settings.sentry`, etc. Each namespace is its own Pydantic model owned by one domain.
**Reasoning:** A single flat `Settings` becomes a 200-field god-object. Namespaces let each service take the slice it needs and keep config evolution per-domain.
**Alternatives considered:**
- *Flat `Settings`* (rejected — bloat, leaks, harder to test).
- *Per-module config files* (rejected — fragments `.env` loading; pydantic-settings handles nested cleanly).
**Consequences:** `.env` keys use double-underscore convention (`WARMING__MAX_PER_DAY=50`). `.env.example` must mirror `core/config.py`; architecture tests enforce this.

### Split `core/db.py` into repositories once tables ≥ 5
**Date:** 2026-06-10
**Status:** Implemented 2026-06-14
**Decision:** Per-aggregate CRUD lives in `core/repositories/<aggregate>.py`. `core/db.py` remains shared SQLite plumbing: metadata, table definitions, engine lifecycle, generic helpers, and compatibility re-exports. Schema evolution is delegated to `core/migrations.py` as a versioned, append-only migration registry.
**Reasoning:** A monolithic `core/db.py` was turning into a god-module. The repository pattern keeps aggregate queries colocated while preserving a single SQLAlchemy gateway.
**Alternatives considered:**
- *Always one file* (rejected — breaks down as tables grow).
- *Repositories from day one* (initially deferred; implemented after the trigger was reached).
**Consequences:** Current repositories include accounts, warming, logs, content, device_fingerprint, dialogues, and spam_status. Existing `from core.db import ...` call sites still work via re-exports, but new aggregate queries should live in `core/repositories/`.

### Astral toolchain (uv + ruff + ty) instead of legacy Python tooling
**Date:** 2026-06-10
**Status:** Active
**Decision:** uv for packaging/venv, ruff for lint+format, ty for type-checking. No pip, venv, black, isort, flake8, pyupgrade, pydocstyle, or mypy.
**Reasoning:** One vendor, Rust-fast, fewer configs, fewer toolchain disagreements.
**Alternatives considered:** Poetry + black + mypy (rejected — slower, more configs); pip + venv + flake8 (rejected — same reason, plus weaker dependency resolution).
**Consequences:** No `requirements.txt` — `pyproject.toml` + `uv.lock`. `ty` is pre-1.0; revisit if it blocks work.

### Strict quality gates
**Date:** 2026-06-16
**Status:** Active
**Decision:** CI and pre-commit enforce ruff, ruff-format, ty, bandit, deptry, vulture, radon, pip-audit, semgrep, and aislop. Aislop is zero-tolerance in CI and pre-push.
**Reasoning:** The project is agent-edited, so structural drift, dead code, weak typing, and generated-code artifacts must fail before merge.
**Alternatives considered:** Manual review only (rejected — too easy to miss drift); relaxed linting (rejected — agentic edits need a hard floor).
**Consequences:** Package splits were driven by size/complexity gates. New work must keep the toolchain green.

### Three-tier logging (loguru file + SQLite logs table + NiceGUI Logs page)
**Date:** 2026-06-10
**Status:** Active
**Decision:** Three sinks: loguru rotating `debug.log` for diagnostic noise; SQLite `logs` table for structured business events; NiceGUI Logs page polling that table. Sentry only for ERROR + unhandled exceptions in production. All access through `core/logging.py`.
**Reasoning:** Operators need a UI filtered by account/status; devs need stacktraces; prod needs alerting. One sink cannot serve all three.
**Alternatives considered:** Only loguru file (rejected — no UI filtering); only SQLite (rejected — stacktraces/debug noise bloat the table); external log aggregator (rejected — overkill for current single-process scope).
**Consequences:** `core.logging.log_event()` is the single business-event gateway. Logging failures are best-effort and must not break business operations.

### `schemas/` is shared types, not a layer — `core/` may import it
**Date:** 2026-06-10
**Status:** Active
**Decision:** `schemas/` contains only Pydantic models with dependencies on Pydantic and typing/stdlib helpers. Both `features/` and `core/` may import from `schemas/`.
**Reasoning:** Public gateways must return Pydantic models. If `core/` cannot import `schemas/`, it would return ORM objects or dicts and force every caller to write mappers.
**Alternatives considered:**
- *Mapper in `features/`* (rejected — duplication and drift).
- *`core/` returns raw dicts* (rejected — violates Pydantic-boundary rule).
- *Invert dependency: `schemas/` depends on `core/db.py`* (rejected — import cycles and impure schemas).
**Consequences:** `schemas/` must stay strictly pure — no `core/`, no `features/`, no I/O libraries.

### Use NiceGUI instead of a split frontend/backend
**Date:** 2026-06-10
**Status:** Superseded 2026-06-28 — see *Frontend/backend split — React SPA over a FastAPI JSON API*.
**Decision:** UI and server run as a single async Python process via NiceGUI.
**Reasoning:** No separate JS frontend, no API contract to maintain, one event loop shares app runtime state.
**Alternatives considered:** FastAPI + React (rejected — two deployables), Streamlit (rejected — weaker for long-running interactive workflows).
**Consequences:** Long-running work must be async and must not block the UI loop.

### Use SQLite (via SQLAlchemy) as the only data store
**Date:** 2026-06-10
**Status:** Active
**Decision:** Single SQLite file for accounts, events, runtime state, and settings.
**Reasoning:** Current scale fits comfortably; one file to back up; trivial local dev; SQLAlchemy keeps the door open to Postgres later.
**Alternatives considered:** Postgres (rejected — operational overhead unjustified at this scale), raw sqlite3 (rejected — loses portability to a bigger DB).
**Consequences:** Single-process assumption. If concurrency becomes a problem, migrate before sharding.

### Feature-per-module/package with no cross-feature imports
**Date:** 2026-06-10
**Status:** Updated 2026-06-16
**Decision:** Each feature owns one module or package under `features/`. Small features can be a single file; larger features become a package with a thin `__init__.py` and focused submodules. Feature domains cannot import each other.
**Reasoning:** The original one-file rule prevented premature structure, but the accounts and warming feature files grew past healthy limits. Package-per-feature keeps isolation while avoiding god-files.
**Alternatives considered:** Force one file forever (rejected — file-size/complexity gates fail); a shared features utility module (rejected — shared logic belongs in services/core/schemas).
**Consequences:** New behavior may extend an existing feature package only when it belongs to that same feature domain. Cross-domain shared logic still moves to `services/`, `core/`, or `schemas/`.

### Pydantic schemas as the only inter-layer carrier
**Date:** 2026-06-10
**Status:** Active
**Decision:** All function inputs/outputs that cross a layer boundary are Pydantic models in `schemas/`. No raw dicts or raw list/tuple payloads.
**Reasoning:** Validation at every edge, types for free, prevents drift between UI / core / DB representations.
**Alternatives considered:** dataclasses (rejected — weaker validation), TypedDict (rejected — no runtime guarantees), raw dicts (rejected — root cause of drift bugs).
**Consequences:** Adding a feature usually means adding or extending a schema first. Multi-item returns should use wrapper response models.
