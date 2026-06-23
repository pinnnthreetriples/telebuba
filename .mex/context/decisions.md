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
last_updated: 2026-06-23
---

# Decisions

## Decision Log

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
**Status:** Active
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
