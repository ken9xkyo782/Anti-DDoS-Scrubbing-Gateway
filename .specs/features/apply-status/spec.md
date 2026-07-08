# Apply-status State Machine Specification

**Milestone:** M1 — Control-plane foundation & tenant model
**Category ID:** APLY
**Status:** Spec drafted; awaiting approval → Design
**Depends on:**
- **Service, rule & list management** (`.specs/features/service-rule-list/`) — consumes the
  `ProtectedService.apply_status` / `version` / `active_version` columns and the `version` bump it owns
  (A-SRL-3); **modifies** its `services`/`rules`/`lists` services to enqueue an apply job after the
  committed `pending` write (documented cross-feature edit, mirroring service-rule-list's own modification
  of tenant-cidr's `revoke`). Requires service-rule-list executed first.
- **Auth & RBAC** (`.specs/features/auth-rbac/`) — reuses `get_current_user`, `require_admin`, and the
  tenant-ownership guard (`authorize_tenant_resource`/`scope_to_tenant`) for the status read + retry +
  admin job-list endpoints, unchanged.
- **Redis** (AD-008 `redis.asyncio`, `compose.test.yml`) — an **enqueue-only** client is added here; the
  consuming worker is M4.

**Discuss context:** `.specs/features/apply-status/context.md` (D-APLY-1..3, A-APLY-1..6)

## Problem Statement

service-rule-list persists config and leaves every change at `apply_status=pending` (version N) — but
nothing yet moves that change toward the data-plane or tells the operator whether it went live. This
feature delivers the **state machine** that carries a committed change `pending → queued → applying →
active` (or `failed`, keeping the last-good config live), the **API-side enqueue** (`pending → queued` +
a durable `AgentJob`), the **worker-facing transition functions** the M4 worker will call, and the
**read API** that surfaces current version, active version, and apply status to the UI (PRD 9.2, TDD
4.5). It is the control-plane half of config propagation; M4 supplies the worker that does the actual
map build and `active_slot` swap. Nothing here touches the data-plane.

## Goals

- [ ] A single **guarded transition function** encoding the legal state graph
      (`pending→queued→applying→active|failed`, plus supersede-to-`pending` and monotonic
      `active_version`), reused by both the API and the future M4 worker; illegal transitions are rejected,
      not silently applied (TDD 4.5).
- [ ] **Auto-enqueue** (D-APLY-1): every committed service/rule/list mutation creates a durable,
      version-idempotent `AgentJob`, transitions the service `pending → queued`, and the mutating endpoint
      returns **202** with `{apply_status: queued, version, active_version}` (TDD 4.6).
- [ ] **Worker-facing transitions** (D-APLY-2): `mark_applying` / `mark_active` / `mark_failed`, each
      version-guarded so a stale in-flight result can never regress newer config ("no stale-over-new
      swap"); fully exercisable in M1 without a data-plane.
- [ ] **Per-service** apply-status (D-APLY-3): status/version/active_version on `ProtectedService`,
      service-scoped rule/list edits rolling up into the parent service; a **read API** surfacing
      status + `last_error` + `last_applied_at` (PRD 9.2), tenant-scoped, plus an **admin** job/backlog
      list (TDD 9.1).
- [ ] A `failed` apply **keeps the previous `active_version` live** — protection continuity, never a gap
      (PRD 8.3).

## Out of Scope

Explicitly excluded to prevent scope creep.

| Feature | Reason |
| --- | --- |
| Worker **loop** / job consumption / BPF map build / verify / `active_slot` swap | M4 *Worker sync & threat feed*; this feature provides the transition functions the worker calls, not the worker |
| Global-blacklist and **threat-feed** apply-status targets | M4 (D-APLY-3); v1 tracks **per-service** only — no generic `ApplyTarget` |
| The remaining job types `FEED_SYNC` / `MAP_REBUILD` / `ACTIVE_SLOT_SWAP` / `TELEMETRY_AGGREGATE` | M4; v1 enqueues one per-service `SERVICE_UPDATE`-class job (A-APLY-2) |
| End-to-end **≤5 s propagation** verification | Requires the M4 worker + data-plane; this feature verifies the state machine + enqueue only |
| One-click **rollback to previous version** (OP-05) | GA/M7; `active_version` history is preserved so it can be built later |
| **Job cancellation** of a superseded in-flight apply | v1 relies on the version guard instead (A-APLY-3) |
| **Alerting** on `apply_status=failed` / worker backlog | M6 *Alerting* (this feature only records the state) |
| Dashboard rendering of apply status | M5; this feature provides the read API, not the UI |
| `version` bump ownership | Owned by service-rule-list (A-SRL-3); this feature **reads** it |

---

## User Stories

### P1: Apply-status state machine & transition guard ⭐ MVP

**User Story**: As the system owner, I want one authoritative definition of the apply states and their
legal transitions, so that every actor (the API now, the M4 worker later) moves config through the same
machine and no illegal or regressive transition is possible.

**Why P1**: TDD 4.5 defines this machine as the contract for config propagation; making it a single
guarded function (not logic scattered across API + worker) is what lets M1 build and test it before the
worker exists (D-APLY-2).

**Acceptance Criteria**:

1. WHEN any transition is requested THEN the system SHALL permit it only if it is in the legal set —
   `pending→queued`, `queued→applying`, `applying→active`, `applying→failed`, and `{queued|applying|active|failed}→pending`
   (supersede) — and SHALL reject any other transition (e.g. `pending→active`, `queued→active`,
   `failed→active`) with an error rather than silently applying it. `(APLY-01)`
2. WHEN an `applying→active` transition succeeds for version K THEN the system SHALL set `active_version=K`,
   and SHALL never allow `active_version` to decrease (monotonic). `(APLY-02)`
3. WHEN either the API or the (future) worker performs a transition THEN it SHALL route through the **same**
   single guard function — the guard is the one source of truth, not reimplemented per caller. `(APLY-03)`
4. WHEN an apply ends in `failed` THEN the system SHALL retain the prior `active_version` unchanged (the
   last-good config stays live; a failed apply never blanks live protection). `(APLY-04)`
5. WHEN states are persisted THEN the system SHALL use the existing `ProtectedService.apply_status` /
   `version` / `active_version` columns (from service-rule-list) — no duplicate status store. `(APLY-05)`

**Independent Test**: Drive the guard directly: `pending→queued→applying→active` sets `active_version`;
attempt `pending→active` → rejected; attempt to lower `active_version` → rejected; `applying→failed`
leaves `active_version` untouched.

---

### P1: Auto-enqueue on mutation (pending → queued) + AgentJob ledger + 202 ⭐ MVP

**User Story**: As a service owner, I want my config change to start propagating the moment I save it and
to see it as "queued", so that I don't need a separate publish step and know a change is on its way to the
data-plane.

**Why P1**: D-APLY-1; TDD 4.5/4.6 — the API enqueues on the mutation and returns `202 (queued)`. This is
the transition service-rule-list explicitly stops short of (A-SRL-3).

**Acceptance Criteria**:

1. WHEN a service/rule/list mutation commits at version N (leaving the service `pending`, per
   service-rule-list) THEN the system SHALL create an `AgentJob` for (`target_type=service`,
   `target_id`, `version=N`) and transition the service `pending → queued`. `(APLY-06)`
2. WHEN such a mutation completes THEN the mutating endpoint SHALL return **202 Accepted** with
   `{apply_status: "queued", version: N, active_version: <unchanged>}`. `(APLY-07)`
3. WHEN a mutation's transaction rolls back THEN the system SHALL create **no** `AgentJob` and perform no
   transition — no phantom job for an uncommitted change. `(APLY-08)`
4. WHEN wiring the enqueue THEN the system SHALL do so by **modifying** service-rule-list's
   `services`/`rules`/`lists` services to call a shared `apply.enqueue_service_update(...)` after the
   committed `pending` write — not by reimplementing the mutation. `(APLY-09)`
5. WHEN a **service-scoped** rule or whitelist/blacklist entry is mutated THEN the enqueue SHALL target the
   **parent service's** version (per-service granularity, D-APLY-3) — one job per affected service, not
   per child row. `(APLY-10)`
6. WHEN an `AgentJob` is created THEN the system SHALL push it to Redis for the (M4) worker; the absence of
   any consumer in M1 SHALL be expected and harmless (the job waits in the queue/ledger). `(APLY-11)`

**Independent Test**: Create a service → response is 202 `queued`, version 1, `active_version=null`, one
`AgentJob(service, v1)` in the ledger + Redis; add a rule → 202 `queued` at version 2, a second job; force
the mutation txn to roll back → no job created.

---

### P1: Worker-facing transitions — applying / active / failed (version-guarded) ⭐ MVP

**User Story**: As the M4 worker (future caller), I want transition functions that advance a job to
applying/active/failed and refuse to apply a result whose version has been superseded, so that a slow
apply can never overwrite a newer config.

**Why P1**: D-APLY-2 — these are the interface M4 consumes; version-guarding them here is what enforces
"no stale-over-new swap" (PRD 8.3, TDD 4.5) and makes the machine complete + testable in M1.

**Acceptance Criteria**:

1. WHEN `mark_applying(job)` is called for a queued job THEN the system SHALL transition the service
   `queued → applying` for that version and record the job's `started_at`. `(APLY-12)`
2. WHEN `mark_active(job, version=K)` is called AND the service's current `version` still equals K THEN the
   system SHALL transition `applying → active`, set `active_version=K`, and record `last_applied_at`.
   `(APLY-13)`
3. WHEN `mark_failed(job, error)` is called THEN the system SHALL transition `applying → failed`, record
   `last_error` + the job's `finished_at`, and leave `active_version` unchanged (APLY-04). `(APLY-14)`
4. WHEN `mark_active`/`mark_failed` is called for a version K that is **older** than the service's current
   `version` (a newer mutation has superseded it) THEN the transition SHALL be a **no-op** — the stale
   result is dropped, the service keeps its newer `pending/queued` state (no stale-over-new swap).
   `(APLY-15)`
5. WHEN M1 ships THEN these functions SHALL be fully exercisable via tests **without any data-plane**;
   M4's worker SHALL only call them (it adds no new transition logic). `(APLY-16)`
6. WHEN a transition changes state THEN the corresponding `AgentJob` row SHALL record its `status` and
   timestamps (`created_at`/`started_at`/`finished_at`) consistently with the service's state. `(APLY-17)`

**Independent Test**: Enqueue a job at v1 → `mark_applying` → applying; `mark_active(v1)` → active,
`active_version=1`, `last_applied_at` set. Separately: enqueue v1, bump to v2 (supersede), then
`mark_active(v1)` → no-op (service stays at v2 queued, `active_version` still null). `mark_failed` path →
failed + `last_error`, `active_version` untouched.

---

### P1: Supersede on new mutation (no stale-over-new) ⭐ MVP

**User Story**: As a service owner editing rapidly, I want each save to supersede the previous in-flight
change, so that the data-plane always converges to my latest config and never to a stale intermediate.

**Why P1**: Auto-enqueue (D-APLY-1) means rapid edits produce multiple in-flight versions; the machine must
guarantee the latest wins without cancellation logic (A-APLY-3, PRD 8.3).

**Acceptance Criteria**:

1. WHEN a service in any state (`queued`/`applying`/`active`/`failed`) is mutated THEN the system SHALL set
   it to `pending` at version N+1 and then `queued` (re-enqueued) — the supersede transition. `(APLY-18)`
2. WHEN a mutation supersedes an in-flight job THEN the system SHALL **not** cancel or wait for that job;
   correctness SHALL come solely from the version guard (APLY-15). `(APLY-19)`
3. WHEN a newer version is pending/queued/applying THEN `active_version` SHALL remain at the last
   successfully-applied version until a newer apply reaches `active` — so the UI legitimately shows
   `version=N` with `active_version=M` where M < N. `(APLY-20)`
4. WHEN several mutations occur in rapid succession THEN each SHALL bump `version` and enqueue a job, and
   the worker SHALL advance `active_version` only to the latest applied version (intermediate versions are
   superseded, idempotent-by-version — APLY-26). `(APLY-21)`

**Independent Test**: Service at `applying` (v1); mutate → v2 `pending→queued`; `mark_active(v1)` now
no-ops (APLY-15); `mark_active(v2)` → `active_version=2`. `active_version` never took the value 1.

---

### P1: Per-service apply-status read API ⭐ MVP

**User Story**: As a service owner (and as an admin over the node), I want to see each service's current
version, active version, and apply status, so that I know what is live and whether the last change went
through (PRD 9.2).

**Why P1**: PRD 9.2 / TDD 9.1 require the UI to surface current active version + last apply status; this
feature owns the read that backs it.

**Acceptance Criteria**:

1. WHEN a caller reads a service THEN the response SHALL include `apply_status`, `version`,
   `active_version`, `last_error` (nullable), and `last_applied_at` (nullable). `(APLY-22)`
2. WHEN a caller requests a service's apply detail THEN the system SHALL surface the state of its
   most-recent `AgentJob` (in-flight or last-finished) alongside the service fields. `(APLY-23)`
3. WHEN a `tenant_user` reads apply-status THEN the system SHALL scope it to their own services (reusing
   the ownership guard; cross-tenant → 404, zero leak). `(APLY-24)`
4. WHEN an `admin` reads apply-status THEN the system SHALL return it for any service (node-view input,
   TDD 9.1), annotated with owning tenant. `(APLY-25)`

**Independent Test**: acme user GETs their service → sees `apply_status`, `version`, `active_version`,
`last_error=null`; after `mark_failed`, `last_error` populated; beta user GET acme's service apply-status
→ 404.

---

### P1: Idempotent, at-least-once, durable enqueue ⭐ MVP

**User Story**: As the system owner, I want enqueue to be durable and idempotent, so that a retry or a
Redis hiccup never loses an apply nor double-applies one.

**Why P1**: TDD 4.5 reliability requirement ("idempotent by `job_id`/version; retry must not duplicate or
swap old-over-new"); the `AgentJob` ledger is the transactional source of truth (A-APLY-1).

**Acceptance Criteria**:

1. WHEN a job is enqueued for (`target_type`, `target_id`, `version`) that already has an `AgentJob` THEN
   the system SHALL treat it as an idempotent no-op (unique per target+version) — enqueuing the same
   version twice does not create a duplicate. `(APLY-26)`
2. WHEN the config write commits but the Redis push fails THEN the system SHALL NOT lose the apply: the
   `AgentJob` ledger row (written with the config change) persists as the source of truth and the push is
   **recoverable** (re-pushable) — the mutation still commits (A-APLY-1). `(APLY-27)`
3. WHEN a job is retried or delivered more than once THEN the version guard (APLY-15) + version-unique
   ledger (APLY-26) together SHALL guarantee "no stale-over-new swap" and "no double-apply". `(APLY-28)`

**Independent Test**: Enqueue (service, v1) twice → one `AgentJob`. Simulate Redis-push failure after
commit → config saved, ledger row present, service `queued`, apply re-pushable (not lost).

---

### P2: Retry a failed apply

**User Story**: As a service owner, I want to retry a failed apply without re-editing the config, so a
transient build/worker failure is one click to recover.

**Why P2**: Recovery UX; the machine already supports it cheaply (A-APLY-6). Not required to unblock the
core pipeline.

**Acceptance Criteria**:

1. WHEN a caller retries a service in `failed` THEN the system SHALL re-enqueue its **current** version and
   transition `failed → queued` (no config change, no version bump). `(APLY-29)`
2. WHEN retry is invoked THEN it SHALL be idempotent — reusing/re-pushing the `AgentJob` for that version,
   creating no duplicate (APLY-26). `(APLY-30)`

**Independent Test**: Drive a service to `failed` (v3); retry → `queued` at v3, same job re-pushed;
`mark_active(v3)` → active.

---

### P2: Admin job / backlog list

**User Story**: As an `admin`, I want to list apply jobs by status, so I can watch the worker backlog and
spot stuck/failed applies for the node view (TDD 9.1).

**Why P2**: Feeds the admin node panel (worker job status / backlog); the per-service read (APLY-22)
already covers the common case.

**Acceptance Criteria**:

1. WHEN an `admin` lists jobs filtered by status (`queued`/`applying`/`failed`) THEN the system SHALL
   return the matching `AgentJob`s (target service, version, timestamps, error). `(APLY-31)`
2. WHEN a `tenant_user` calls the job-list endpoint THEN the system SHALL reject with 403 (`require_admin`);
   their own services' status is available via the per-service read (APLY-24). `(APLY-32)`

**Independent Test**: Create backlog (jobs at `queued`/`failed`); admin GET `/jobs?status=failed` → the
failed one; tenant_user GET `/jobs` → 403.

---

## Edge Cases

- WHEN the worker reports success (`mark_active`) for a version that no longer matches the service's
  current version THEN the swap SHALL be dropped as stale (APLY-15) — the service keeps its newer state.
  `(APLY-33)`
- WHEN the same version is processed more than once (retry / at-least-once redelivery / two workers) THEN
  `active_version` SHALL be set at most once to that version and re-application SHALL be a no-op (idempotent
  by version + monotonic `active_version`). `(APLY-34)`
- WHEN a mutation arrives while the service is `applying` THEN the version bumps to N+1 and re-enqueues; the
  in-flight result for the old version is dropped on completion (APLY-15) and the new job proceeds
  independently. `(APLY-35)`
- WHEN Redis is unavailable at enqueue time THEN the mutation SHALL still commit and the service SHALL be
  recorded `queued` in the ledger (recoverable push), not fail with a write error (A-APLY-1). `(APLY-36)`
- WHEN a brand-new service is created (version 1, `active_version=null`) THEN it SHALL enqueue and show
  `queued`; `active_version` stays `null` until the **first** successful apply (consistent with the
  `enabled=false` default — not yet live). `(APLY-37)`
- WHEN a service is **disabled or enabled** (itself a mutation) THEN it SHALL flow through the machine like
  any other change (a new version the worker applies) — disable propagates as `service_disabled` drop-all
  in the data-plane (M2), enable likewise; disable is **not** a bypass. `(APLY-38)`
- WHEN an apply `failed`s THEN the last `active_version` config SHALL remain live (protection continuity);
  the failure is visible via `apply_status=failed` + `last_error` (and alerted in M6). `(APLY-39)`
- WHEN `version` or `active_version` would move backward THEN the system SHALL reject it — both are
  monotonic per service (rollback, OP-05, is a *forward* re-apply of older config as a new version, out of
  scope here). `(APLY-40)`

---

## Requirement Traceability

| Requirement ID | Story | PRD/AD/TDD ref | Phase | Status |
| --- | --- | --- | --- | --- |
| APLY-01..05 | P1: State machine & transition guard | TDD 4.5, PRD 8.3 | - | Pending |
| APLY-06..11 | P1: Auto-enqueue + AgentJob ledger + 202 | TDD 4.5/4.6, D-APLY-1, A-SRL-3 | - | Pending |
| APLY-12..17 | P1: Worker-facing transitions (version-guarded) | TDD 4.5, PRD 8.3, D-APLY-2 | - | Pending |
| APLY-18..21 | P1: Supersede on new mutation | PRD 8.3, A-APLY-3 | - | Pending |
| APLY-22..25 | P1: Per-service apply-status read API | PRD 9.2, TDD 9.1, D-APLY-3 | - | Pending |
| APLY-26..28 | P1: Idempotent / at-least-once / durable enqueue | TDD 4.5, A-APLY-1 | - | Pending |
| APLY-29..30 | P2: Retry a failed apply | A-APLY-6 | - | Pending |
| APLY-31..32 | P2: Admin job / backlog list | TDD 9.1 | - | Pending |
| APLY-33..40 | Edge cases | TDD 4.5, PRD 8.3, OP-05, AD-002 | - | Pending |

**ID format:** `APLY-[NUMBER]`
**Status values:** Pending → In Design → In Tasks → Implementing → Verified
**Coverage:** 40 requirements total, 0 mapped to tasks yet (Tasks phase pending) ⚠️

**Cross-feature:** **reads** service-rule-list's `version` (owned there, A-SRL-3) and **modifies** its
service/rule/list services to enqueue (APLY-09, same modification pattern service-rule-list used on
tenant-cidr's `revoke`); reuses auth-rbac's ownership guard + `require_admin`; adds an enqueue-only Redis
client + the `AgentJob` model consumed by the **M4** worker.

---

## Success Criteria

- [ ] Every transition is validated by one shared guard: the legal graph is enforced and illegal /
      backward-`active_version` transitions are rejected — proven by a direct unit table over the guard.
- [ ] A committed service/rule/list mutation returns **202** `queued`, creates exactly one durable
      version-unique `AgentJob`, and pushes it to Redis; a rolled-back mutation creates none.
- [ ] The `mark_applying`/`mark_active`/`mark_failed` functions drive the machine end-to-end **without a
      data-plane**, and a stale-version result is a no-op (integration test: supersede then apply-old →
      no swap; apply-new → `active_version` advances).
- [ ] A `failed` apply leaves the previous `active_version` live; `version`/`active_version` are monotonic.
- [ ] The read API surfaces `apply_status`/`version`/`active_version`/`last_error`/`last_applied_at`,
      tenant-scoped (cross-tenant → 404); the admin job-list is admin-only (tenant_user → 403).
- [ ] Enqueue is idempotent by (target, version) and durable across a Redis-push failure (mutation not
      lost) — proven by an integration test.

---

## Decisions & Assumptions (flagged for confirmation)

See `context.md` for full rationale.
1. **D-APLY-1** — **auto-enqueue** on every mutation (202 `queued`), not an explicit apply/publish action.
2. **D-APLY-2** — this feature owns the **state machine + guard + real Redis enqueue + `AgentJob` ledger +
   worker-facing transition functions**; M4 adds only the worker loop.
3. **D-APLY-3** — **per-service** apply targets in v1; global-blacklist/feed apply-status deferred to M4
   (no generic `ApplyTarget`).
4. **A-APLY-1** — Redis outage is **graceful-degrade via the ledger** (mutation commits, apply
   recoverable), not fail-the-write; the `AgentJob` ledger is a transactional outbox. Confirm mechanism in
   Design.
5. **A-APLY-2** — one per-service `SERVICE_UPDATE`-class job in v1 (triggering change kind recorded for
   observability); other job types are M4.
6. **A-APLY-3** — the **version guard** is the only concurrency control (no job cancellation).
   **A-APLY-4** — category `APLY`, dir `apply-status`. **A-APLY-5** — read = extend service GET + admin
   job-list. **A-APLY-6** — retry-failed is P2; rollback (OP-05) deferred.
