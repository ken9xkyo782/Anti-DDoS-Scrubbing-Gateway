# Agent Worker & Job Pipeline Specification

**Milestone:** M4 — Worker sync & threat feed
**Category ID:** AGW
**Status:** Spec APPROVED (2026-07-10); context complete (D-AGW-1..2) → Design
**Depends on:**
- **Apply-status state machine** (`.specs/features/apply-status/`, **executed** — commits
  `a4b1ffd..de47b5f`) — this feature is the *consumer half* of that machine: it consumes the durable
  `AgentJob` ledger + the `apply:jobs` Redis list (`APPLY_QUEUE_KEY`), and drives every transition
  exclusively through the executed `mark_applying` / `mark_active` / `mark_failed` (version-guarded,
  `core/applystate.py` guard) and the `retry` path. It adds **no transition logic** (APLY-03 honored).
- **Auth & RBAC** (`.specs/features/auth-rbac/`, executed) — reuses the app-lifespan Redis client
  (`core/redis.py`) and settings/config conventions; no new endpoints, no RBAC change.
- **Service, rule & list management** (`.specs/features/service-rule-list/`, executed) — the worker
  reads the committed service/rule/list config from PostgreSQL at apply time (TDD 4.5 "đọc config đầy
  đủ"); models unchanged.
- **No data-plane dependency in this feature** — the map build/verify/`active_slot` swap is M4 #2
  (*Double-buffer map build/swap*), reached through an **applier boundary** this feature defines
  (GA-1). Executable independently of M3 *Fairness* Execute (no shared files).

**Discuss context:** `.specs/features/agent-worker/context.md` (D-AGW-1: v1 placeholder applier —
`active` = "acknowledged by worker" until M4 #2; D-AGW-2: orphaned-`applying` auto-recovery via
`mark_failed` + existing retry path; A-AGW-1..8).

## Problem Statement

M1 left committed config changes durably queued: every mutation writes a version-idempotent `AgentJob`
row, best-effort-LPUSHes its id to `apply:jobs`, and returns 202 — but **nothing consumes the queue**.
Jobs sit `queued` forever; `apply_status` never reaches `active`; the ≤5 s propagation promise (TDD 4.5)
has no engine. This feature delivers that engine: a long-running Python **agent worker** (PRD 6.8) that
consumes jobs from Redis, reconciles undelivered jobs from the DB ledger (the outbox's promised consumer,
APLY-27/36), dispatches them to per-type handlers, and drives `queued → applying → active | failed`
through the executed version-guarded `mark_*` functions — idempotent under duplicate delivery, safe under
restart, never swapping stale over new. The actual BPF map build/swap arrives in M4 #2 behind an applier
boundary defined here.

## Goals

- [ ] A **worker process** that consumes `apply:jobs` with a blocking pop (no busy-poll) and processes a
      dispatched job within ~1 s of LPUSH; a dispatched `SERVICE_UPDATE` reaches a terminal state
      (`active`/`failed`/`superseded`) in **≤ 5 s** end-to-end under nominal conditions (TDD 4.5).
- [ ] The **DB ledger is the queue of record**: jobs whose Redis dispatch was lost (`dispatched_at IS
      NULL`, worker down, Redis down) are discovered and applied by a **startup + periodic reconcile
      sweep** — Redis is only the low-latency path (A-APLY-1 honored).
- [ ] **Reliability invariants** (PRD 6.8): idempotent by `job_id`/version (duplicate delivery = no-op);
      no stale-over-new swap (superseded versions never invoked against the applier); **worker restart
      preserves active state** (startup never rebuilds/swaps anything unsolicited).
- [ ] A **job-pipeline shape M4 #2/#3 and M5 plug into**: a handler registry keyed by `JobType`, a
      `SERVICE_UPDATE` handler that reads full config from PostgreSQL at apply time and invokes a
      well-defined **applier boundary** (the seam M4 #2 replaces with the real double-buffer build/swap).
- [ ] **Operational hygiene**: transient infra failure (Redis/DB down) degrades with bounded backoff —
      never a crash-loop, never converted into job failures; handler failure is captured on the job
      (`error`, truncated) with the service left `failed` and retryable; per-job structured logs.

## Out of Scope

Explicitly excluded to prevent scope creep.

| Feature | Reason |
| --- | --- |
| BPF map build, verify, `active_slot` swap, rollback flip, bpffs access | M4 #2 *Double-buffer map build/swap* — reached via the applier boundary defined here (GA-1) |
| `FEED_SYNC` handler, feed scheduling, feed validation/normalize/dedup | M4 #3 *Threat intelligence feed sync* |
| `MAP_REBUILD` / `ACTIVE_SLOT_SWAP` job types + handlers | M4 #2 (they exist only once a real applier exists) |
| `TELEMETRY_AGGREGATE` job type + handler | M5 *Telemetry & dashboards* |
| `RULE_UPDATE` / `LIST_UPDATE` as distinct job types | Already realized as `SERVICE_UPDATE` + `trigger` discriminator (M1 design, A-APLY-2); PRD 6.8's table is satisfied per-service |
| New API endpoints / dashboard surfaces | M1's `GET /services/{id}/apply-status` + admin `GET /jobs` already expose everything the worker writes; UI = M5 |
| Alerting on worker-down / backlog / stuck jobs | M6 *Alerting* (this feature only makes the state observable) |
| Multi-worker scale-out, work sharding, leases | Single-node pilot; v1 = one worker process (A-AGW-2; guards make a second worker safe but it is unsupported) |
| Redis Stream / consumer-group upgrade | Deferred behind the M1 `ApplyDispatcher` boundary decision; list + ledger meets v1 reliability |
| Job cancellation | v1 relies on the version guard (A-APLY-3, unchanged) |

---

## Gray Areas (RESOLVED 2026-07-10 → context.md)

**GA-1 → D-AGW-1:** v1 ships a **succeeding placeholder applier** — the full
`queued→applying→active` lifecycle runs end-to-end now; `active` means "acknowledged by worker" until
M4 #2 fills the applier boundary with the real double-buffer build/swap (D-SLRD-1 interim-writer
precedent).

**GA-2 → D-AGW-2:** orphaned-`applying` jobs **auto-recover** in the startup sweep via
`mark_failed("worker restarted mid-apply")` + the existing `retry` path (`failed→queued`,
re-dispatch) — zero new state-machine edges, visible `failed` blip, bounded by `attempts`.

---

## User Stories

### P1: Worker process & job consumption loop ⭐ MVP

**User Story**: As a service owner, I want my committed config change picked up and driven to a terminal
apply state within seconds, so that the 202 `queued` I received actually goes somewhere without any
manual step.

**Why P1**: This is the engine of TDD 4.5 — without consumption, M1's state machine is inert and the
≤5 s propagation target is unmeasurable.

**Acceptance Criteria**:

1. WHEN the worker is running and a job id is LPUSHed to `apply:jobs` THEN the worker SHALL pick it up
   within ~1 s using a blocking pop (no busy-poll tight loop). `(AGW-01)`
2. WHEN a job is picked up THEN the worker SHALL call `mark_applying(job_id)` **before** running any
   handler, and SHALL run the handler only if the job survived it (not superseded / not already
   terminal). `(AGW-02)`
3. WHEN the handler completes successfully THEN the worker SHALL call `mark_active(job_id)`; WHEN the
   handler raises THEN the worker SHALL call `mark_failed(job_id, error)` with the exception message
   (truncated to the M1 limit) — the service is left `failed` and retryable, `active_version`
   untouched. `(AGW-03)`
4. WHEN the worker performs any state change THEN it SHALL route exclusively through the executed
   `mark_applying`/`mark_active`/`mark_failed`/`retry` functions — it SHALL NOT write `apply_status`,
   `active_version`, or `AgentJob.status` directly (APLY-03 single-guard honored). `(AGW-04)`
5. WHEN a dispatched `SERVICE_UPDATE` is processed under nominal conditions (worker up, DB+Redis up)
   THEN the elapsed time from LPUSH to the job's terminal state SHALL be ≤ 5 s (integration-asserted
   with the v1 applier). `(AGW-05)`
6. WHEN the queue is empty THEN the worker SHALL keep waiting indefinitely (blocking pop with timeout →
   loop), with no log spam and no exit. `(AGW-06)`

**Independent Test**: Start worker against compose PG+Redis; commit a service mutation via the API;
observe `GET /services/{id}/apply-status` go `queued→applying→active` with `active_version=N` in ≤ 5 s.

---

### P1: SERVICE_UPDATE handler & applier boundary ⭐ MVP

**User Story**: As the system owner, I want job execution shaped as *registry → handler → applier* so
that M4 #2 (map build/swap), M4 #3 (feed sync), and M5 (telemetry) each plug in a handler/applier without
touching the pipeline.

**Why P1**: The pipeline is this feature's lasting contract; the applier boundary is the seam the rest
of M4 fills (same seam discipline as the data-plane features).

**Acceptance Criteria**:

1. WHEN a job is executed THEN the worker SHALL select its handler from a registry keyed by `JobType`;
   `SERVICE_UPDATE` SHALL be the one registered handler in this feature. `(AGW-07)`
2. WHEN a job's `JobType` has no registered handler THEN the worker SHALL `mark_failed` it with a
   distinct "no handler" error and continue with the next job — never crash, never leave it
   `applying`. `(AGW-08)`
3. WHEN the `SERVICE_UPDATE` handler runs THEN it SHALL read the **full current config** for the target
   service (service + plan + rules + whitelist + blacklist) from PostgreSQL at apply time — the job
   carries identity (`target`, `version`), not payload; rapid successive edits therefore collapse
   naturally (TDD 4.5). `(AGW-09)`
4. WHEN the handler has the config THEN it SHALL invoke a single **applier boundary** (build+activate
   contract for one service at one version) whose v1 implementation follows **GA-1**; M4 #2 replaces
   the implementation, not the boundary. `(AGW-10)`
5. WHEN the applier reports failure THEN the handler SHALL raise → `mark_failed`, and the last-good
   `active_version` SHALL remain live (APLY-04 end-to-end). `(AGW-11)`

**Independent Test**: Register a recording/fake applier in tests: assert it is invoked with the target's
current config + version; make it raise → job `failed`, service `failed`, `active_version` unchanged;
enqueue a job with an unregistered type → job `failed` ("no handler"), worker still consumes next job.

---

### P1: Durable pickup — DB-ledger reconcile (the outbox's consumer) ⭐ MVP

**User Story**: As an operator, I want every committed change applied even if Redis was down or the
worker was offline when it was made, so that the queue-of-record promise from M1 is actually kept.

**Why P1**: A-APLY-1/APLY-27/36 explicitly deferred "M4 reconciles undelivered rows from the DB" to this
feature; without it, Redis is a single point of loss.

**Acceptance Criteria**:

1. WHEN the worker starts THEN it SHALL sweep the ledger for actionable jobs (`queued` — whether or not
   `dispatched_at` is set — and orphaned `applying` per GA-2) and process them before/alongside live
   consumption; no committed job is stranded by a missed LPUSH. `(AGW-12)`
2. WHEN the worker is running THEN it SHALL periodically (env-tunable interval, default small enough to
   honor a degraded-mode propagation bound of ≤ 60 s) re-sweep for `queued` jobs with
   `dispatched_at IS NULL` (Redis was down at dispatch time) and process them. `(AGW-13)`
3. WHEN Redis is unavailable THEN the worker SHALL degrade to DB-poll-only operation with bounded
   backoff (no crash-loop, no job failures caused by the outage) and SHALL resume blocking consumption
   when Redis returns. `(AGW-14)`
4. WHEN PostgreSQL is unavailable THEN the worker SHALL retry with bounded backoff and SHALL NOT
   consume-and-drop Redis entries it cannot process (an unprocessable pop must remain recoverable via
   the ledger sweep). `(AGW-15)`
5. WHEN a popped job id does not exist in the ledger (e.g. service hard-deleted → CASCADE removed its
   jobs) THEN the worker SHALL log and skip it — never crash. `(AGW-16)`
6. WHEN a job id is delivered more than once (Redis + reconcile overlap, retry re-dispatch, at-least-once
   redelivery) THEN the second processing SHALL be a harmless no-op (terminal-job guard) — exactly-once
   *effect* on `active_version`. `(AGW-17)`

**Independent Test**: Stop Redis; commit a mutation (202, `dispatched_at` NULL); start worker with only
DB up → job goes `active` within the reconcile interval. Separately: LPUSH the same job id twice → one
advance, second no-op.

---

### P1: No stale-over-new under churn ⭐ MVP

**User Story**: As a service owner making rapid successive edits, I want exactly my newest version to end
up active, so that an older in-flight apply can never clobber a newer one.

**Why P1**: PRD 6.8's core reliability invariant; the version guard exists (M1) but this feature is its
first real caller under concurrency — the invariant must hold end-to-end through the worker.

**Acceptance Criteria**:

1. WHEN `mark_applying` reports the job superseded (service `version` > job `version`) THEN the worker
   SHALL NOT invoke the handler/applier for that job and SHALL move on (job ends `superseded`). `(AGW-18)`
2. WHEN a mutation commits version N+1 while the worker is mid-apply on version N THEN version N's
   terminal mark SHALL be a no-op (existing guard) and the worker SHALL subsequently process N+1's job
   to `active` — final state: `active_version = N+1`, exactly one advance per version. `(AGW-19)`
3. WHEN K rapid edits produce jobs v1..vK THEN after the worker drains the queue the service SHALL be
   `active` at `active_version = vK`, with every older job terminal (`succeeded` or `superseded`) —
   never a stale version live, regardless of delivery order. `(AGW-20)`

**Independent Test**: Enqueue v-N, pause the applier mid-apply (test hook), commit v-N+1, release: N's
mark is a no-op (`superseded`), N+1 applies; assert `active_version = N+1` and no double-advance.

---

### P2: Restart safety & orphan recovery

**User Story**: As an operator, I want to be able to restart or lose the worker at any moment without
losing live protection or stranding in-flight changes, so that the worker is operationally boring.

**Why P2**: "Worker restart không được làm mất trạng thái active hiện tại" (PRD 11.3); recovery makes
crashes self-limiting but the P1 pipeline is demoable without it.

**Acceptance Criteria**:

1. WHEN the worker starts THEN it SHALL NOT modify any service's `apply_status`/`active_version` other
   than through processing actionable jobs — and (binding on M4 #2's applier too) startup SHALL NOT
   trigger any unsolicited rebuild or slot swap: active state is preserved. `(AGW-21)`
2. WHEN the startup sweep finds jobs stuck at `applying` (orphaned by a crash) THEN it SHALL resolve
   them per **GA-2**, and the resolution SHALL be visible in the job's history (`error`/`attempts`).
   `(AGW-22)`
3. WHEN the worker receives SIGTERM/SIGINT THEN it SHALL stop consuming new jobs, allow the in-flight
   job a bounded grace period to finish its terminal mark, and exit cleanly; a job that cannot finish
   in time is left `applying` for the GA-2 startup path of the next run. `(AGW-23)`
4. WHEN the worker is killed uncleanly (SIGKILL / power loss) at **any** point in the pipeline THEN a
   subsequent start SHALL converge every affected job to a terminal state with `active_version` correct
   (at-least-once + guards + GA-2 = crash-consistent). `(AGW-24)`

**Independent Test**: Kill -9 the worker between `mark_applying` and the terminal mark; restart; assert
the orphaned job resolves per GA-2 and a fresh mutation still applies normally.

---

### P2: Operational visibility & configuration

**User Story**: As an operator, I want the worker's activity observable from logs and the existing admin
API, and its knobs settable per environment, so that I can run and debug it without reading source.

**Why P2**: M5 dashboards and M6 alerting build on this state; v1 needs enough to operate the pilot.

**Acceptance Criteria**:

1. WHEN a job reaches a terminal state THEN the worker SHALL emit one structured log line carrying at
   least `job_id`, `job_type`, `target_id`, `version`, outcome, duration, and attempt number; handler
   tracebacks go to logs (full) and `AgentJob.error` (truncated). `(AGW-25)`
2. WHEN the worker starts THEN it SHALL log its effective configuration (queue key, poll timeout,
   reconcile interval, backoff bounds) once. `(AGW-26)`
3. WHEN an operator inspects the system THEN the existing admin `GET /jobs?status=` SHALL reflect
   everything the worker did (backlog, in-flight, terminal states) — this feature adds **no** new
   endpoints. `(AGW-27)`
4. WHEN deploying THEN the worker SHALL be startable as a dedicated process entrypoint using the same
   env-driven settings module as the API (12-factor; documented run command; `compose.test.yml`
   compatible). `(AGW-28)`
5. WHEN tuning THEN blocking-pop timeout, reconcile interval, backoff bounds, and shutdown grace SHALL
   be env-tunable with documented defaults; defaults SHALL satisfy AGW-05's ≤ 5 s nominal bound.
   `(AGW-29)`

**Independent Test**: Run the worker with non-default env values; assert the startup config log reflects
them; drive one success + one failure; assert both appear in `GET /jobs` and as structured log lines.

---

### P3: Retry re-dispatch integration

**User Story**: As a service owner whose apply failed, I want the existing retry button to actually
re-run the job now that a consumer exists, so that recovery is one click rather than an SSH session.

**Why P3**: The M1 `retry` endpoint already re-queues + re-dispatches; this story only proves the loop
closes through the worker. Nice-to-have as an explicit story; largely falls out of P1.

**Acceptance Criteria**:

1. WHEN a `failed` service's `POST /services/{id}/apply-status/retry` is called THEN the worker SHALL
   pick up the re-dispatched job and drive it to a terminal state exactly like a fresh job (idempotency
   guards unchanged). `(AGW-30)`

**Independent Test**: Force a handler failure (fake applier raises), retry via the API with the applier
fixed, assert the service reaches `active` at the same version.

---

## Edge Cases

- WHEN the popped Redis payload is not a UUID THEN the worker SHALL log and skip it (defensive; only our
  dispatcher writes this list). `(AGW-16 family)`
- WHEN a job is already terminal when popped (reconcile processed it first) THEN the pop SHALL be a
  no-op. `(AGW-17)`
- WHEN a second worker process is started by mistake THEN correctness SHALL be preserved (all effects
  behind `FOR UPDATE` + version + terminal guards) — duplicate processing is wasted work, not corruption;
  single-worker remains the supported deployment (A-AGW-2).
- WHEN the DB is reachable but a `mark_*` raises unexpectedly (bug-class error) THEN the worker SHALL
  log with full context and continue with the next job (one poisonous job must not wedge the pipeline);
  the job itself remains recoverable via sweep/GA-2.
- WHEN the reconcile sweep and the blocking pop race on the same job THEN guards make one of them a
  no-op (no double handler invocation for the same *terminal outcome*; a benign double `applying`
  attempt is tolerated by design).

## Assumptions (flagged, not user-decided)

- **A-AGW-1**: The worker lives in the **control-plane package** as a separate process entrypoint
  (e.g. `python -m app.worker`), importing `services/apply` + models directly — the shape the M1 design
  assumed ("worker calls mark_*"). Pilot deploys it colocated on the gateway node (M4 #2 needs bpffs
  access from this process). No HTTP hop between worker and control-plane logic.
- **A-AGW-2**: **One worker process, one job at a time** in v1. No sharding/leases; guards make a stray
  second worker safe but unsupported.
- **A-AGW-3**: The dispatch channel stays the M1 **Redis list** (`BRPOP`); a Stream/consumer-group
  upgrade remains deferred behind the dispatcher boundary.
- **A-AGW-4**: `JobType` stays **`SERVICE_UPDATE`-only** in v1 (A-APLY-2). The registry + "no handler →
  failed" rule (AGW-08) is the forward-compat contract; M4 #2/#3 and M5 add their enum values +
  handlers in their own features. PRD 6.8's `RULE_UPDATE`/`LIST_UPDATE` rows are realized as
  `SERVICE_UPDATE` + `trigger` (M1 decision, unchanged).
- **A-AGW-5**: The job carries **identity, not payload** — config is read from PostgreSQL at apply time
  (TDD 4.5). No payload schema is introduced.
- **A-AGW-6**: `attempts` accounting stays where M1 put it (`mark_applying` increments); the worker adds
  no separate attempt counter.
- **A-AGW-7**: The ≤ 5 s bound (AGW-05) is asserted with the v1 applier; M4 #2 re-validates it with real
  map builds (its own requirement).
- **A-AGW-8**: Worker liveness/backlog **alerting** is M6; v1 observability = structured logs + the
  existing admin jobs API (AGW-25/27).

---

## Requirement Traceability

| Requirement ID | Story | Phase | Status |
| --- | --- | --- | --- |
| AGW-01 | P1: Consumption loop | Execute | Verified |
| AGW-02 | P1: Consumption loop | Execute | Verified |
| AGW-03 | P1: Consumption loop | Execute | Verified |
| AGW-04 | P1: Consumption loop | Execute | Verified |
| AGW-05 | P1: Consumption loop | Execute | Verified |
| AGW-06 | P1: Consumption loop | Execute | Implemented |
| AGW-07 | P1: Handler & applier boundary | Execute | Verified |
| AGW-08 | P1: Handler & applier boundary | Execute | Verified |
| AGW-09 | P1: Handler & applier boundary | Execute | Verified |
| AGW-10 | P1: Handler & applier boundary | Execute | Verified |
| AGW-11 | P1: Handler & applier boundary | Execute | Verified |
| AGW-12 | P1: Ledger reconcile | Execute | Verified |
| AGW-13 | P1: Ledger reconcile | Execute | Verified |
| AGW-14 | P1: Ledger reconcile | Execute | Implemented; manual check pending |
| AGW-15 | P1: Ledger reconcile | Execute | Implemented |
| AGW-16 | P1: Ledger reconcile | Execute | Verified |
| AGW-17 | P1: Ledger reconcile | Execute | Verified |
| AGW-18 | P1: No stale-over-new | Execute | Verified |
| AGW-19 | P1: No stale-over-new | Execute | Verified |
| AGW-20 | P1: No stale-over-new | Execute | Verified |
| AGW-21 | P2: Restart & orphan recovery | Execute | Verified |
| AGW-22 | P2: Restart & orphan recovery | Execute | Verified |
| AGW-23 | P2: Restart & orphan recovery | Execute | Verified |
| AGW-24 | P2: Restart & orphan recovery | Execute | Verified |
| AGW-25 | P2: Visibility & configuration | Execute | Verified |
| AGW-26 | P2: Visibility & configuration | Execute | Verified |
| AGW-27 | P2: Visibility & configuration | Execute | Verified |
| AGW-28 | P2: Visibility & configuration | Execute | Verified |
| AGW-29 | P2: Visibility & configuration | Execute | Verified |
| AGW-30 | P3: Retry re-dispatch | Execute | Verified |

**Coverage:** 30 total, 30 mapped to executed tasks. AGW-14's destructive
Redis-outage check remains a documented manual verification.

---

## Success Criteria

- [ ] Commit a service mutation via the API with the worker running → `GET
      /services/{id}/apply-status` shows `active` with `active_version = N` in **≤ 5 s** (AGW-05).
- [ ] Stop Redis, commit a mutation, start only DB + worker → the change still reaches a terminal state
      within the reconcile bound (≤ 60 s degraded) — zero lost jobs across the outage (AGW-13/14).
- [ ] `kill -9` the worker mid-apply, restart → the orphaned job converges per GA-2, active state
      untouched, and a fresh mutation applies normally (AGW-21/22/24).
- [ ] K rapid edits → exactly `active_version = vK`; every older job terminal; no double-advance under
      duplicate delivery (AGW-17/19/20).
- [ ] The full control-plane test suite (M1 baseline) still passes; new worker integration tests run on
      `compose.test.yml` PG+Redis with a fake/recording applier.
