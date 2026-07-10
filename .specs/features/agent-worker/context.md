# Agent Worker & Job Pipeline — Context (Discuss output)

**Spec:** `.specs/features/agent-worker/spec.md` (AGW-01..30)
**Captured:** 2026-07-10 (discuss within Specify)
**Status:** Ready for design

---

## Feature Boundary

The **consumer half** of the config-propagation machine M1 built. Apply-status (executed,
`a4b1ffd..de47b5f`) left committed changes durably queued — an `AgentJob` ledger row written in the
mutation's transaction plus a best-effort `LPUSH` to `apply:jobs` — with the explicit promise that "the
M4 worker reconciles undelivered rows from the DB" (A-APLY-1). This feature is that worker:

- **Owns:** the long-running worker process, blocking-pop consumption of `apply:jobs`, the startup +
  periodic **DB-ledger reconcile sweep** (undispatched `queued` rows + orphaned `applying` rows), the
  **handler registry** keyed by `JobType`, the `SERVICE_UPDATE` handler (read full config from
  PostgreSQL at apply time), and a single **applier boundary** whose v1 implementation is a
  placeholder (D-AGW-1).
- **Calls, never reimplements:** every state change routes through the executed
  `mark_applying`/`mark_active`/`mark_failed`/`retry` (version-guarded; `core/applystate.py` is still
  the one guard — APLY-03).
- **Does not own:** BPF map build/verify/`active_slot` swap (M4 #2 fills the applier boundary), feed
  sync (M4 #3), telemetry aggregation (M5), alerting (M6), new API endpoints (M1's read surfaces
  already show everything the worker writes).

Executable independently of M3 *Fairness* Execute — pure control-plane, no shared files with the
data-plane track.

---

## Implementation Decisions

Two gray areas on the M4 #1 ↔ M4 #2 seam were resolved with the user after the spec draft (2026-07-10).

### D-AGW-1: v1 applier = succeeding placeholder — the full lifecycle runs end-to-end now

**Question:** The `SERVICE_UPDATE` handler must end every job in `active` or `failed`, but the real map
build/swap is the next feature (M4 #2). What does "applied" mean until then?
**Decision:** Ship a **placeholder applier** that reads the target's full config, logs what it would
build (service + plan + rules + lists at version N), and **succeeds** — so `queued → applying → active`
runs end-to-end from this feature onward, `active_version` advances, and every pipeline invariant
(reconcile, churn, crash recovery, idempotency) is integration-tested against the real state machine.
Until M4 #2 lands, `active` means "**acknowledged by the worker**"; M4 #2 replaces the applier
*implementation* (not the boundary) and re-validates the ≤5 s bound with real map builds (A-AGW-7).
**Why:** Mirrors the established interim-writer precedent (D-SLRD-1 seed helper: this-feature-testable
now, authoritative writer next feature). The alternatives are worse: leaving jobs non-terminal makes
nothing demoable and defers every invariant test to M4 #2; pulling a "minimal real map write" forward
drags double-buffer/verify design into the wrong feature.
**Trade-off:** For the window between this feature's Execute and M4 #2's, the UI's `active` overstates
reality (no data-plane change actually occurred). Accepted: pilot-internal, documented at the applier
boundary, and the data-plane is meanwhile still driven by the loader's env seed (D-SLRD-1 posture
unchanged).
**Impact:** `AGW-03/05/10/11`; the applier boundary contract (one service, one version, full config in →
success/failure out) becomes the M4 #2 fill-in point; the ≤5 s success criterion is asserted with this
applier.

### D-AGW-2: Orphaned-`applying` jobs auto-recover on startup (fail → existing retry path)

**Question:** A crash between `mark_applying` and the terminal mark leaves job + service stuck at
`applying` — the Redis entry is already consumed and `LEGAL_APPLY` has no `applying → queued` edge. How
does the startup sweep resolve these orphans?
**Decision:** **Auto-recover using existing edges only:** the startup sweep calls
`mark_failed(job, "worker restarted mid-apply")` and then immediately drives the existing **retry** path
(`failed → queued` + re-dispatch) for that service — self-healing with **zero new state-machine
transitions**, a brief `failed` blip visible in job history, and the `attempts` counter bounding the
loop. A subsequent crash-recover cycle on the same job is naturally superseded the moment a newer
mutation lands (version guard).
**Why:** Keeps the ≤5 s propagation promise approximately intact across crashes (no human in the loop
for a routine restart) while touching nothing in M1's frozen transition table. Manual-only retry would
strand in-flight changes after every crash; lease/heartbeat reclaim is machinery for a multi-worker
world v1 explicitly doesn't have (A-AGW-2).
**Trade-off:** The recovery consumes one visible `failed` + one `attempts` increment per crash (the job
history shows the blip — a feature, not a bug); a *wedged-but-alive* worker (not crashed, not
progressing) is **not** detected by this mechanism — that is M6's stuck-job/backlog alerting, not v1's
problem.
**Impact:** `AGW-12/22/24`; the sweep's retry call is a **system-actor** invocation of the M1 `retry`
service path — Design decides how the audit line for a system-initiated retry is attributed
(vs the user-initiated `apply.retry` audit).

---

## Flagged assumptions (written into spec, confirm during Design)

- **A-AGW-1 — Worker = separate process in the control-plane package** (e.g. `python -m app.worker`),
  importing `services/apply` + models directly — the shape the M1 design assumed ("worker calls
  mark_*"). Pilot deploys it colocated on the gateway node (M4 #2 needs bpffs from this process). No
  HTTP hop between worker and control-plane logic.
- **A-AGW-2 — One worker, one job at a time** in v1. No sharding/leases; guards make a stray second
  worker safe but unsupported.
- **A-AGW-3 — Dispatch channel stays the M1 Redis list** (`BRPOP` on `APPLY_QUEUE_KEY`); Stream upgrade
  stays deferred behind the `ApplyDispatcher` boundary.
- **A-AGW-4 — `JobType` stays `SERVICE_UPDATE`-only** (A-APLY-2 unchanged). The registry + "no handler →
  `mark_failed`" rule (AGW-08) is the forward-compat contract; M4 #2/#3 and M5 add their enum values +
  handlers in their own features. PRD 6.8's `RULE_UPDATE`/`LIST_UPDATE` rows are realized as
  `SERVICE_UPDATE` + `trigger`.
- **A-AGW-5 — Jobs carry identity, not payload** — config is read from PostgreSQL at apply time
  (TDD 4.5 "đọc config đầy đủ"); rapid edits collapse naturally. No payload schema.
- **A-AGW-6 — `attempts` accounting stays in `mark_applying`** (M1); the worker adds no separate
  counter.
- **A-AGW-7 — The ≤5 s bound is asserted with the v1 placeholder applier**; M4 #2 re-validates with
  real map builds as its own requirement.
- **A-AGW-8 — Worker liveness/backlog alerting is M6**; v1 observability = structured logs + the
  existing admin `GET /jobs` (AGW-25/27).

---

## Specific References

- **A-APLY-1/APLY-27/36** (transactional outbox: ledger is the queue of record, "M4 reconciles
  undelivered rows") — the contract AGW-12..17 fulfils.
- **APLY-03** (single guard, both callers) and the executed `mark_*` version guards (no stale-over-new,
  APLY-13/15/33/34) — the worker adds no transition logic; AGW-18..20 are the first concurrent
  exerciser.
- **D-SLRD-1** (interim seed-helper writer; authoritative build = M4) — the precedent D-AGW-1 mirrors;
  the loader env seed remains the data-plane's writer until M4 #2.
- **TDD 4.5** (worker sequence: consume idempotent by job_id/version → read full config → build →
  swap/fail; ≤5 s), **TDD 6 / PRD 6.8** (job table + reliability: idempotent, swap-only-on-full-build,
  restart preserves active state), **PRD 11.3** (worker restart must not lose active state).
- **AD-008** (`redis.asyncio`, `compose.test.yml` PG+Redis) — the worker's test substrate.
- **AD-017** (bpffs pin pattern "the M4 worker will reuse") — relevant to M4 #2's applier, cited here
  only to keep the boundary honest (this feature never touches pins).

---

## Deferred Ideas

- **Redis Stream / consumer groups** behind `ApplyDispatcher` (multi-worker, replay) — GA.
- **Lease/heartbeat stuck-job reclaim** for a wedged-but-alive worker — pairs with M6 stuck-job
  alerting.
- **Worker health endpoint / metrics export** (beyond logs + `GET /jobs`) — M5 telemetry decides the
  surface.
- **Backpressure / batch coalescing** (skip straight to the newest queued version per service instead
  of superseding one job at a time) — optimization, only meaningful under sustained churn.
