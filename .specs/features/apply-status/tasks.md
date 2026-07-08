# Apply-status State Machine Tasks

**Design**: `.specs/features/apply-status/design.md`
**Spec**: `.specs/features/apply-status/spec.md` (APLY-01..40)
**Context**: `.specs/features/apply-status/context.md` (D-APLY-1..3; A-APLY-1..6)
**Testing**: `.specs/codebase/TESTING.md`
**Status**: Draft — awaiting approval → Execute

**Cross-feature prerequisite:** requires **Service, rule & list management (T1–T11)** executed first (and
transitively **Auth & RBAC** + **Tenant & CIDR allocation**). This feature reuses their `control-plane/`
skeleton, the `ProtectedService` model + its `apply_status`/`version`/`active_version` columns + the
`ApplyStatus` enum, `app/services/services.py::bump_version`, `app/core/deps.py`
(`load_service_for_principal`, `require_admin`, `authorize_tenant_resource`), `app/services/audit.py`, and
the **Redis client** wired into `app/main.py` lifespan (same instance as the session store — namespaced
`apply:*` keys). It **modifies** service-rule-list's `services`/`rules`/`lists` services + `bump_version`
and their routers. Its Alembic revision's `down_revision` = **service-rule-list's head**.

**Stack for all tasks:** async FastAPI (asyncpg + SQLAlchemy 2.0 `AsyncSession`, `redis.asyncio`), httpx
`AsyncClient` tests, ruff + mypy. Integration tests need `compose.test.yml` (PG + Redis) up. Apply the
`coding-guidelines` skill during implementation. Only unit-tested tasks may be `[P]` (AD-008 / TESTING.md —
the shared compose stack serializes integration).

---

## Execution Plan

### Phase 1 — Foundation (T1 parallel with T2)
```
T1 [P]   (unit — pure transition guard, no I/O)
T2       (integration — agent_job table + unique/index/FK + migration)
```

### Phase 2 — Apply service: produce + read (Sequential)
```
{T1,T2} ──► T3   (enqueue outbox + ApplyDispatcher + get_apply_status/list_jobs)
```

### Phase 3 — Transitions & mutation wiring (Sequential — shared compose stack)
```
T3 ──► T4        (mark_applying/active/failed + retry — version-guarded)
{T1,T3} ──► T6   (wire enqueue+dispatch into service-rule-list services + bump_version)
```

### Phase 4 — API surface (Sequential)
```
{T3,T4} ──► T5   (apply-status read + retry + admin /jobs router)
T6      ──► T7   (service-rule-list routers → 202 queued)
```

**Dependency graph**
```
T1 [P] ─┬───────────────► T3 ─┬─► T4 ─────► T5
        └───────────────► T6  │       ▲
T2 ─────────────────────► T3 ─┤       │
                              ├───────┘ (T5 also reuses T3 reads)
                              └─► T6 ─────► T7
```
Additional edges captured in the cross-check table: **T1→T6** (guard used in `bump_version`), **T3→T5**
(reads), **T3→T6** (enqueue+dispatcher).

---

## Task Breakdown

### T1: Apply-status transition guard (pure) [P]
**What**: `app/core/applystate.py` — the single source of truth for legal transitions, zero I/O:
`LEGAL_APPLY: dict[ApplyStatus, frozenset[ApplyStatus]]` (the transition table from design);
`assert_transition(current, target)` raising `IllegalTransition` unless `target in LEGAL_APPLY[current]`;
`assert_active_version_advances(active_version, new)` raising `NonMonotonicVersion` unless `new >
(active_version if not None else -1)`; `is_terminal(job_status) -> bool` (`succeeded`/`failed`/
`superseded`). Plus the `IllegalTransition` / `NonMonotonicVersion` exception types. Reuses the existing
`ApplyStatus` enum; `JobStatus` may be forward-referenced or imported once T2 lands (keep `is_terminal`
accepting the enum).
**Where**: `control-plane/app/core/applystate.py`, `control-plane/tests/unit/test_applystate.py`
**Depends on**: None
**Reuses**: `ApplyStatus` (service-rule-list `models`); establishes the transition contract (design "Apply-state guard")
**Requirement**: APLY-01 (legal set), APLY-02 (monotonic), APLY-03 (single guard), APLY-05 (reuses enum), APLY-34 (`is_terminal`)
**Tools**: Bash, Write/Edit · Skill: `coding-guidelines`
**Done when**:
- [ ] `assert_transition` truth table: every legal edge passes (`pending→queued`, `queued→applying`, `applying→active`, `applying→failed`, `failed→queued`, and `{queued,applying,active,failed}→pending`); a representative set of illegal edges raises `IllegalTransition` (`pending→active`, `queued→active`, `failed→active`, `active→applying`) (APLY-01)
- [ ] `assert_active_version_advances`: `None→N` ok, `M→N (N>M)` ok, `N→N` and `N→N-1` raise `NonMonotonicVersion` (APLY-02)
- [ ] `is_terminal` true for `succeeded`/`failed`/`superseded`, false for `queued`/`applying` (APLY-34)
- [ ] Gate check passes: `ruff check . && ruff format --check . && mypy app/ && pytest -q -m unit` (quick)
- [ ] Test count: ≥8 tests pass (no silent deletions)
**Tests**: unit
**Gate**: quick
**Commit**: `feat(apply): pure apply-status transition guard (legal graph + monotonic active_version)`

---

### T2: AgentJob model, constraints & migration
**What**: Add to `app/db/models.py`: `AgentJob` + enums (`JobStatus`={queued,applying,succeeded,failed,superseded}, `JobType`={service_update}, `ChangeTrigger`={service,plan,rule,whitelist,blacklist,enable,disable}). Constraints: **`agent_job_target_version_unique`** = `UNIQUE(target_type, target_id, version)` (idempotency, APLY-26); `Index("ix_agent_job_status","status")`; `Index("ix_agent_job_target","target_type","target_id")`. FK `agent_job.target_id → protected_service.id ON DELETE CASCADE`. Columns per design (`error`, `attempts` default 0, `dispatched_at`, `created_at`, `started_at`, `finished_at`). Hand-written Alembic revision, `down_revision` = **service-rule-list head**. No new columns on `protected_service` (`last_error`/`last_applied_at` are derived on read).
**Where**: `control-plane/app/db/models.py` (modify), `control-plane/migrations/versions/*_apply_status.py`, `control-plane/tests/integration/test_agent_job_model.py`
**Depends on**: None (reuses `Base`, `ProtectedService`)
**Reuses**: `Base`, `ProtectedService`; design "Data Models"
**Requirement**: APLY-05 (reuse service columns), APLY-11 (job schema), APLY-17 (timestamps), APLY-26 (unique idempotency)
**Tools**: Bash, Write/Edit · Skill: `coding-guidelines`
**Done when**:
- [ ] `alembic upgrade head` creates `agent_job` with a constraint literally named `agent_job_target_version_unique` (verify via `pg_constraint`) and both indexes (APLY-26)
- [ ] Inserting two `agent_job` rows with the same `(target_type, target_id, version)` → second raises `IntegrityError` (APLY-26)
- [ ] Deleting a `protected_service` cascades its `agent_job` rows (FK CASCADE)
- [ ] `alembic downgrade -1` drops the table cleanly; `down_revision` chains off service-rule-list's head
- [ ] Gate check passes: `ruff check . && ruff format --check . && mypy app/ && pytest -q` (full)
- [ ] Test count: ≥4 tests pass (no silent deletions)
**Tests**: integration
**Gate**: full
**Commit**: `feat(apply): AgentJob model + agent_job table & migration`

---

### T3: Apply service — enqueue outbox, dispatcher & reads
**What**: `app/services/apply.py` — `enqueue_service_update(db, service, actor, trigger) -> AgentJob` (asserts service is `pending`, `assert_transition(pending, queued)`, `INSERT agent_job(...status=queued) ON CONFLICT (target_type,target_id,version) DO NOTHING` in the caller's txn, `service.apply_status=queued`, registers `job.id` for post-commit dispatch; per-service target — APLY-06/08/10/26); `ApplyDispatcher.dispatch(job_id)` (post-commit `LPUSH apply:jobs`, then `UPDATE agent_job SET dispatched_at=now()`; on Redis error **log and return**, never raise — APLY-11/27/36); `get_apply_status(db, service) -> ApplyStatusView` (assembles `apply_status`, `version`, `active_version`, `last_error`, `last_applied_at`, `latest_job` from the service + its latest `agent_job` — APLY-22/23/25); `list_jobs(db, *, status=None) -> Sequence[AgentJob]` (APLY-31). Domain error `NotFailedError` (for retry, used by T4) declared here or in the service's errors module.
**Where**: `control-plane/app/services/apply.py`, `control-plane/tests/integration/test_apply_enqueue.py`
**Depends on**: T1, T2
**Reuses**: guard (T1), `AgentJob`/`ProtectedService` (T2), Redis client (lifespan), `ApplyStatus`; design "Apply service" + "Apply dispatcher" + "Transactional outbox"
**Requirement**: APLY-06, APLY-08, APLY-10, APLY-11, APLY-22, APLY-23, APLY-25, APLY-26, APLY-27, APLY-31, APLY-36
**Tools**: Bash, Write/Edit · Skill: `coding-guidelines`
**Done when**:
- [ ] `enqueue_service_update` on a `pending` service (version N) creates exactly one `agent_job(target='service', version=N, status='queued')` and sets `service.apply_status='queued'` in the same txn (APLY-06/10); a mutation whose txn is rolled back leaves **zero** jobs (APLY-08)
- [ ] Enqueuing the same `(service, version)` twice → one row (`ON CONFLICT DO NOTHING`, APLY-26)
- [ ] `dispatch` performs `LPUSH apply:jobs <job_id>` and sets `dispatched_at`; with Redis unreachable the call **does not raise**, the job row persists `dispatched_at IS NULL` (recoverable), and the caller's mutation still commits (APLY-11/27/36)
- [ ] `get_apply_status` returns `{apply_status, version, active_version, last_error, last_applied_at, latest_job}` for a service (APLY-22/23); admin may read any service's view (APLY-25)
- [ ] `list_jobs(status=...)` filters by job status (APLY-31)
- [ ] Gate check passes: `ruff check . && ruff format --check . && mypy app/ && pytest -q` (full)
- [ ] Test count: ≥8 tests pass (no silent deletions)
**Tests**: integration
**Gate**: full
**Commit**: `feat(apply): enqueue outbox, dispatcher & apply-status reads`

---

### T4: Apply service — version-guarded transitions & retry
**What**: Extend `app/services/apply.py` (from T3) with the worker-facing transitions, each `SELECT … FOR UPDATE` on the service **and** the job row (the same lock `bump_version` takes): `mark_applying(db, job_id)` (terminal-job → no-op; `service.version != job.version` → job `superseded`; else `assert_transition(queued, applying)`, `service.apply_status=applying`, `job.status=applying`, `started_at`, `attempts+=1` — APLY-12); `mark_active(db, job_id)` (`assert_transition(applying, active)` + `assert_active_version_advances`; `apply_status=active`, `active_version=job.version`, `job.status=succeeded`, `finished_at` — APLY-13/02/17; stale/terminal → no-op — APLY-15/33/34); `mark_failed(db, job_id, error)` (`applying→failed`, `job.status=failed`, `job.error=error[:2000]`, `finished_at`, `active_version` untouched — APLY-14/04/39); `retry(db, service, actor)` (409 `NotFailedError` unless `apply_status==failed`; `assert_transition(failed, queued)`; reset the current-version job to `queued`, clear `error`/`started_at`/`finished_at`/`dispatched_at`; `audit.record_event(action="apply.retry")`; register dispatch — APLY-29/30).
**Where**: `control-plane/app/services/apply.py` (extend), `control-plane/tests/integration/test_apply_transitions.py`
**Depends on**: T3
**Reuses**: guard (T1), models (T2), `enqueue_service_update`/`ApplyDispatcher` (T3), `audit.record_event`; design "The state machine (crux)"
**Requirement**: APLY-02, APLY-04, APLY-12, APLY-13, APLY-14, APLY-15, APLY-16, APLY-17, APLY-19, APLY-20, APLY-21, APLY-28, APLY-29, APLY-30, APLY-33, APLY-34, APLY-35, APLY-39, APLY-40
**Tools**: Bash, Write/Edit · Skill: `coding-guidelines`
**Done when**:
- [ ] Happy path: `mark_applying`→`mark_active` drives `queued→applying→active`, sets `active_version=N` and `finished_at` (surfaced as `last_applied_at`) (APLY-12/13/17)
- [ ] Supersede: enqueue N, bump service to N+1, `mark_active(job=N)` → **no-op**, job `superseded`, `active_version` unchanged; `mark_active(job=N+1)` → advances (APLY-15/33/19/20)
- [ ] `mark_failed` → `failed` + `error` truncated; `active_version` unchanged (last-good stays live) (APLY-04/14/39)
- [ ] At-least-once: `mark_active` called twice for the same job → one advance, second no-op (`is_terminal`) (APLY-34/28); attempting to lower `active_version` → `NonMonotonicVersion` (APLY-02/40)
- [ ] `retry` on a `failed` service → `failed→queued`, re-dispatch, `apply.retry` audited; `retry` when not `failed` → 409 `NotFailedError` (APLY-29/30)
- [ ] All transitions exercised with **no data-plane** present (APLY-16)
- [ ] Gate check passes: `ruff check . && ruff format --check . && mypy app/ && pytest -q` (full)
- [ ] Test count: ≥9 tests pass (no silent deletions)
**Tests**: integration
**Gate**: full
**Commit**: `feat(apply): version-guarded mark_applying/active/failed & retry`

---

### T5: Apply-status API — read, retry & admin jobs router
**What**: `app/api/routers/apply_status.py` — `GET /services/{id}/apply-status` (`load_service_for_principal` → 404 cross-tenant; returns the `get_apply_status` view — APLY-22/24/25), `POST /services/{id}/apply-status/retry` (owner/admin; calls `apply.retry`; 409 if not `failed` — APLY-29), `GET /jobs?status=` (**`require_admin`** → 403 for tenant_user; `apply.list_jobs` — APLY-31/32). Response schemas in `app/api/schemas/apply.py` (`ApplyStatusView`, `JobView`). Mount in `app/main.py`.
**Where**: `control-plane/app/api/routers/apply_status.py`, `control-plane/app/api/schemas/apply.py`, `control-plane/app/main.py` (modify), `control-plane/tests/integration/test_apply_status_api.py`
**Depends on**: T3, T4
**Reuses**: `get_apply_status`/`list_jobs` (T3), `retry` (T4), `load_service_for_principal`/`require_admin` (auth-rbac + service-rule-list); design "API router"
**Requirement**: APLY-22, APLY-23, APLY-24, APLY-25, APLY-29, APLY-31, APLY-32
**Tools**: Bash, Write/Edit · Skill: `coding-guidelines`
**Done when**:
- [ ] `GET /services/{id}/apply-status` returns `{apply_status, version, active_version, last_error, last_applied_at, latest_job}`; owner/admin ok; another tenant's id → 404 zero-leak (APLY-22/23/24/25)
- [ ] `POST …/retry` on a `failed` service → 202/200 `queued`; on a non-`failed` service → 409 (APLY-29)
- [ ] `GET /jobs?status=failed` (admin) → the failed jobs; `tenant_user` → 403 (APLY-31/32)
- [ ] Gate check passes: `ruff check . && ruff format --check . && mypy app/ && pytest -q` (full)
- [ ] Test count: ≥6 tests pass (no silent deletions)
**Tests**: integration
**Gate**: full
**Commit**: `feat(apply): apply-status read, retry & admin jobs API`

---

### T6: Wire enqueue into service-rule-list services + guard-route bump_version
**What**: Modify service-rule-list services. (a) `app/services/services.py::bump_version` — set `apply_status` via `assert_transition(current, pending)` then `pending` (routes supersede through the guard — APLY-03/18). (b) After `bump_version`, each mutating method calls `apply.enqueue_service_update(db, service, actor, trigger=<kind>)` in the same txn and dispatches **post-commit** (after `await db.commit()`): `create_service` (trigger=service; new service → `queued`, `active_version` stays NULL — APLY-37), `update_service` (service), `size_plan` (plan), `set_enabled` (enable/disable — flows through the machine, APLY-38), rules `create/update/delete` (rule; enqueue targets the **parent** service — APLY-10), lists `add/remove` whitelist/blacklist (whitelist/blacklist; **service scope only** — global blacklist has no per-service target, D-APLY-3). (c) `delete_service` — **no enqueue** (the mandatory preceding disable already enqueued a drop-all job; a hard-deleted service is gone from `service_map`; tombstone `SERVICE_DELETE` for map GC is deferred to M4 — design flag #3).
**Where**: `control-plane/app/services/services.py` (modify), `control-plane/app/services/rules.py` (modify), `control-plane/app/services/lists.py` (modify), `control-plane/tests/integration/test_apply_wiring_services.py`
**Depends on**: T1, T3
**Reuses**: `enqueue_service_update`/`ApplyDispatcher` (T3), guard (T1), `bump_version`; design "Modifications to service-rule-list"
**Requirement**: APLY-03, APLY-06, APLY-08, APLY-09, APLY-10, APLY-18, APLY-21, APLY-35, APLY-37, APLY-38
**Tools**: Bash, Write/Edit · Skill: `coding-guidelines`
**Done when**:
- [ ] `create_service` → service `queued`, one `agent_job(version=1)`, `active_version` NULL; dispatch attempted post-commit (APLY-06/37)
- [ ] A rule mutation and a service-scoped whitelist/blacklist mutation each enqueue against the **parent service's** current version (one job per affected service) (APLY-10); a global-blacklist add enqueues **nothing** (no per-service target, D-APLY-3)
- [ ] `disable`/`enable` flow through the machine (service ends `queued` with a new job) (APLY-38)
- [ ] `bump_version` sets `pending` via `assert_transition` (guard is the single writer of the transition) (APLY-03/18); a mutation while a service is `applying` bumps to N+1 and re-enqueues (superseding the in-flight job — verified against T4's guard) (APLY-35)
- [ ] A mutation whose txn rolls back enqueues **no** job and dispatches nothing (APLY-08); `delete_service` creates no apply job
- [ ] Enqueue is a **modification** of the existing methods, not a reimplementation of the mutation (APLY-09)
- [ ] Gate check passes: `ruff check . && ruff format --check . && mypy app/ && pytest -q` (full)
- [ ] Test count: ≥7 tests pass (no silent deletions)
**Tests**: integration
**Gate**: full
**Commit**: `feat(apply): enqueue apply jobs from service/rule/list mutations`

---

### T7: Service-rule-list routers → 202 queued
**What**: Modify service-rule-list routers (`app/api/routers/{services,rules,lists}.py`) so mutation endpoints return **202 Accepted** with `{apply_status, version, active_version}` (was 201/200 `pending`), reflecting the `queued` state T6 leaves. Read/list endpoints unchanged. (Global-blacklist router stays as-is — no per-service apply target.)
**Where**: `control-plane/app/api/routers/services.py` (modify), `control-plane/app/api/routers/rules.py` (modify), `control-plane/app/api/routers/lists.py` (modify), `control-plane/tests/integration/test_apply_202.py`
**Depends on**: T6
**Reuses**: service-rule-list routers; design "Modifications to service-rule-list" + TDD 4.6 body
**Requirement**: APLY-07, APLY-37, APLY-38
**Tools**: Bash, Write/Edit · Skill: `coding-guidelines`
**Done when**:
- [ ] `POST /services`, `PATCH /services/{id}`, `POST /services/{id}/enable|disable`, `PATCH /services/{id}/plan`, rule create/update/delete, whitelist/blacklist add/remove → **202** with `{apply_status:"queued", version, active_version}` (APLY-07/38)
- [ ] A newly created service returns `active_version: null` at `queued` (APLY-37)
- [ ] Read/list endpoints and the global-blacklist endpoints are unchanged (still 200/201)
- [ ] Gate check passes: `ruff check . && ruff format --check . && mypy app/ && pytest -q` (full)
- [ ] Test count: ≥4 tests pass (no silent deletions)
**Tests**: integration
**Gate**: full
**Commit**: `feat(apply): return 202 queued from config mutation endpoints`

---

## Parallel Execution Map
```
Phase 1:  T1 [P]  ‖  T2                    (T1 unit/no-I/O; T2 integration — no collision)
Phase 2:  {T1,T2} ──► T3
Phase 3:  T3 ──► T4 ;  {T1,T3} ──► T6      (both integration → sequential)
Phase 4:  {T3,T4} ──► T5 ;  T6 ──► T7      (integration → sequential)
```
Only **T1** is `[P]` — the sole unit-tested task (parallel-safe per TESTING.md). Every DB/Redis-touching
task shares the single `compose.test.yml` stack and runs sequentially (AD-008).

---

## Validation — Check 1: Task Granularity

| Task | Scope | Status |
| --- | --- | --- |
| T1 | 1 module, pure guard (3 fns + 2 errors) | ✅ Granular |
| T2 | 1 model + constraints + 1 migration (cohesive schema change) | ✅ Granular |
| T3 | 1 service module — produce/read side (enqueue+dispatch+reads, one concern) | ✅ Granular |
| T4 | same module — transition state-machine fns + retry (one concern) | ✅ Granular |
| T5 | 1 router (+schema/mount) | ✅ Granular |
| T6 | uniform enqueue hook across the 3 existing mutation services (one seam) | ✅ Granular |
| T7 | 202 response flip across the 3 existing routers (one change) | ✅ Granular |

## Validation — Check 2: Diagram ↔ Definition Cross-Check

| Task | Depends on (body) | Diagram arrows | Status |
| --- | --- | --- | --- |
| T1 | None | (root) | ✅ Match |
| T2 | None | (root) | ✅ Match |
| T3 | T1, T2 | T1→T3, T2→T3 | ✅ Match |
| T4 | T3 | T3→T4 | ✅ Match |
| T5 | T3, T4 | T3→T5, T4→T5 | ✅ Match |
| T6 | T1, T3 | T1→T6, T3→T6 | ✅ Match |
| T7 | T6 | T6→T7 | ✅ Match |

Parallel check: `T1 [P]` shares no dependency with `T2` in Phase 1 ✅. No two tasks in any parallel set depend on each other.

## Validation — Check 3: Test Co-location

| Task | Code layer | Matrix requires | Task says | Status |
| --- | --- | --- | --- | --- |
| T1 | pure logic (≈ security primitives) | unit | unit | ✅ OK |
| T2 | models + constraints | integration | integration | ✅ OK |
| T3 | service (+ Redis) | integration | integration | ✅ OK |
| T4 | service | integration | integration | ✅ OK |
| T5 | api router | integration | integration | ✅ OK |
| T6 | service (modify) | integration | integration | ✅ OK |
| T7 | api router (modify) | integration | integration | ✅ OK |

All three checks pass — no restructuring required.

---

## Requirement Coverage

40 requirements (APLY-01..40) all map to tasks:

- APLY-01 (T1) · 02 (T1,T4) · 03 (T1,T6) · 04 (T4) · 05 (T1,T2) · 06 (T3,T6) · 07 (T7) · 08 (T3,T6) · 09 (T6) · 10 (T3,T6) · 11 (T2,T3) · 12 (T4) · 13 (T4) · 14 (T4) · 15 (T4) · 16 (T4) · 17 (T2,T4) · 18 (T4,T6) · 19 (T4) · 20 (T4) · 21 (T4,T6) · 22 (T3,T5) · 23 (T3,T5) · 24 (T5) · 25 (T3,T5) · 26 (T2,T3) · 27 (T3) · 28 (T3,T4) · 29 (T4,T5) · 30 (T4) · 31 (T3,T5) · 32 (T5) · 33 (T4) · 34 (T1,T4) · 35 (T6) · 36 (T3) · 37 (T6,T7) · 38 (T6,T7) · 39 (T4) · 40 (T4).
- **Unmapped:** none. (All P1/P2 stories + edge cases covered.)

**Coverage:** 40 total, **40 mapped to tasks**, 0 unmapped ✅.

Cross-feature: T6/T7 pick up service-rule-list's A-SRL-3 handoff (mutations now end at `queued`, not `pending`); T1's guard + T4's transitions are the interface the **M4 worker** will consume (it adds no transition logic).

---

## Tooling note (Execute phase)

Same uniform greenfield Python project as auth-rbac / tenant-cidr / service-rule-list → built-in **Bash /
Write / Edit** + `pytest`; apply the `coding-guidelines` skill during implementation. No external MCPs
required. Diagrams via `mermaid-studio` (sources in `diagrams/`, already rendered). Execution delegates
each task to a sub-agent (sequential; **T1** may run in parallel with T2), per the Sub-Agent Delegation
model — **only when you approve and start Execute**. Because this feature modifies the service-rule-list
runtime, **service-rule-list (and transitively auth-rbac + tenant-cidr) must be executed before T2
onward** — T2's `down_revision` chains off service-rule-list's head.

**Confirm before Execute:** the design's open flags carry sensible defaults baked into these tasks —
notably **`delete_service` enqueues no job** (T6), the **post-commit dispatch** runs after `await
db.commit()` in the modified service methods (T6), `agent_job.error` truncates at **2000** chars (T4), and
`GET /jobs` is **admin-only node-wide** (T5). Flag any you want changed before Execute.
