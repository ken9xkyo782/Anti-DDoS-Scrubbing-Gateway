# Apply-status State Machine — Context (Discuss output)

**Spec:** `.specs/features/apply-status/spec.md`
**Captured:** 2026-07-08 (discuss within Specify)
**Status:** Ready for design

---

## Feature Boundary

The control-plane state machine that carries a committed config change from *written to the DB* to
*live in the data-plane*, and surfaces that progress to the UI (PRD 9.2). It sits on the seam already
staked out by its neighbours:

- **service-rule-list** (A-SRL-3, confirmed) already writes `apply_status=pending` and **owns the
  `version` bump** on every mutation, and already put `apply_status` / `version` / `active_version`
  columns on `ProtectedService`. It stops at `pending`.
- **M4 worker** (roadmap *Worker sync & threat feed*) owns the actual Redis job **consumption**, BPF
  map build/verify, and the `active_slot` swap.
- TDD 4.5 shows the **FastAPI side** doing the enqueue and returning `202 (apply_status=queued)` — the
  `pending → queued` transition and the enqueue live on the API side, i.e. **this** M1 feature.

So this feature owns: the **state-machine definition + a single guarded transition function**, the
API-side `pending → queued` transition with a **real Redis enqueue** + **`AgentJob` ledger**, the
**worker-facing transition functions** (`applying`/`active`/`failed`, version-guarded) that M4's worker
will call, and the **per-service apply-status read API**. It writes nothing to the data-plane and runs
no worker loop.

---

## Implementation Decisions

Three gray areas on the M1↔M4 seam were resolved with the user before writing the spec.

### D-APLY-1: Auto-enqueue on every mutation (not an explicit apply/publish action)

**Question:** Should a config change enter the pipeline (`pending → queued` + enqueue) automatically on
every mutation, or only when the user triggers an explicit "Apply/Publish" action?
**Decision:** **Auto-enqueue.** Each committed service/rule/list write immediately creates a job and
transitions the service to `queued`; the mutating endpoint returns **202** with `apply_status=queued`,
`version=N`, `active_version` (unchanged). This matches the TDD 4.5 sequence (the `PUT` itself enqueues
and returns `202 queued`).
**Why:** Meets the **≤5 s propagation** goal by construction (no human in the loop); rapid edits collapse
harmlessly at the worker because jobs are **idempotent by version** and `active_version` only advances
(D-APLY-2's version guard). No new UI action or extra state to explain.
**Trade-off:** N rapid edits enqueue N jobs (versions N, N+1, …); the worker only advances `active_version`
to the latest, so intermediate versions are superseded, not separately "applied". An explicit
batch/review-before-apply gate is a **deferred** enhancement, not v1.
**Impact:** `APLY-06..11`. The enqueue wires into service-rule-list's service/rule/list services by a
**documented modification** (same pattern as service-rule-list modifying tenant-cidr's `revoke`), not a
reimplementation of the mutation.

### D-APLY-2: M1 owns the machine + guard + real Redis enqueue + `AgentJob` ledger + worker-facing transitions; M4 adds only the worker loop

**Question:** How much of the state machine does this M1 feature build, versus defer to the M4 worker?
**Decision:** **Machine + enqueue + ledger.** M1 builds: (a) the canonical states + **legal-transition
table** behind a **single guard function** (the one source of truth both the API and the future worker
route every transition through); (b) the API `pending → queued` with a **real Redis enqueue**; (c) an
**`AgentJob` ledger** (idempotent by `job_id`/version, the transactional source of truth); (d) the
**worker-facing** `mark_applying` / `mark_active` / `mark_failed` transition functions (version-guarded);
(e) the **202 contract** + per-service **status read API**. M4's worker is then a thin consumer that only
calls the mark_* functions and does the map build/swap.
**Why:** Maximises what is **verifiable in M1** — the whole machine is unit+integration testable now
without any data-plane (drive the transitions directly). Gives M4 a clean, already-tested interface.
Redis enqueue-only is cheap and already an AD-008 test dependency (`redis.asyncio`, `compose.test.yml`).
Jobs piling up unconsumed until M4 exists is expected and harmless.
**Trade-off:** Redis becomes a real (enqueue-only) dependency in M1, one milestone before its consumer.
The mark_* functions ship "callable but only called by tests" until M4 wires the worker.
**Impact:** `APLY-01..05` (machine/guard), `APLY-12..17` (worker-facing transitions), `APLY-26..28`
(idempotent/at-least-once). New `AgentJob` model + one Alembic revision. Redis enqueue client added.

### D-APLY-3: Per-service apply targets in v1; global-blacklist/feed apply-status deferred to M4

**Question:** The roadmap says apply-status is tracked "per service/list/feed." What actually carries a
status in v1?
**Decision:** **Per-service only.** `apply_status` / `version` / `active_version` stay on
`ProtectedService` (already there). **Service-scoped** whitelist/blacklist and rule edits **roll up into
the parent service's** version (already how service-rule-list bumps it), so they flow through the
service's machine. The **global blacklist** (`service_id=NULL`) and **threat feeds** get their **own**
apply-status in **M4**, where their build machinery (bloom/LPM rebuild, feed fetch) actually lands.
**Why:** The service model already supports it — no speculative `ApplyTarget` abstraction for targets
that have **no data-plane consumer yet** in M1. Global-list and feed application are genuinely M4/M3
concerns; introducing their status rows now would be modeling ahead of need.
**Trade-off:** A global-blacklist admin edit in this milestone does **not** get its own apply-status row
(it is a plain list write per service-rule-list's `SRL-28..30`); its propagation tracking arrives with the
feed/list build in M4. "Per service/list/feed" in the roadmap reduces to **per-service** for v1.
**Impact:** `APLY-10`, `APLY-22..25`. No `ApplyTarget` table; `AgentJob.target_type` carries `service`
only in v1 (enum left open for `global_list` / `feed` in M4).

---

## Flagged assumptions (written into spec, confirm during Design)

- **A-APLY-1 — Redis outage is graceful-degrade, not fail-the-write.** The chosen `AgentJob` ledger is a
  **transactional outbox**: the ledger row is written in the same txn as the `pending` config change and
  is the source of truth; the Redis push happens post-commit. If Redis is down the **mutation still
  commits** (config saved, service `queued` in the ledger) and the apply is **recoverable** (re-pushable
  by a reconciler / the worker on reconnect) — the write is never lost. Alternative (fail the mutation
  with 503 when Redis is down) is rejected as it couples config persistence to queue availability.
  Confirm the outbox mechanism (vs a two-phase enqueue) in Design. (`APLY-08`, `APLY-27`, `APLY-36`)
- **A-APLY-2 — One per-service job kind in v1.** Because targets are per-service (D-APLY-3), a service,
  rule, or list edit all mean "rebuild service X to version N" — carried as a single `SERVICE_UPDATE`-class
  job. The triggering change kind (service/rule/list) is recorded on the `AgentJob` for observability, but
  the worker treats them uniformly. TDD's separate `RULE_UPDATE`/`LIST_UPDATE`/`FEED_SYNC`/`MAP_REBUILD`/
  `ACTIVE_SLOT_SWAP` job types are an M4 concern. Confirm the v1 job-type set. (`APLY-06`, `APLY-11`)
- **A-APLY-3 — Version guard is the *only* concurrency control.** A superseding mutation does **not**
  cancel or wait for an in-flight job; correctness comes from the worker's `mark_active`/`mark_failed`
  being **no-ops when their version no longer matches the service's current version** ("no stale-over-new
  swap"), and `active_version` being monotonic. No job cancellation in v1. (`APLY-15`, `APLY-19`,
  `APLY-34`)
- **A-APLY-4 — Category ID:** `APLY` (Apply-status). Feature directory `apply-status`.
- **A-APLY-5 — Read surface = extend the existing service GET + add an admin job-list.** The per-service
  status fields already ride on the service response (`SRL-05`); this feature enriches it with `last_error`
  / `last_applied_at` / in-flight job state and adds an **admin-only** `AgentJob` list for the
  worker/backlog node view (TDD 9.1). No separate per-service status sub-resource unless Design prefers it.
  Confirm. (`APLY-22..25`, `APLY-31..32`)
- **A-APLY-6 — Retry-failed is P2; rollback is deferred.** A minimal "retry a `failed` apply" (re-enqueue
  the current version, `failed → queued`) is P2 because the machine already supports it cheaply. One-click
  **rollback to a previous version** (OP-05) is GA/M7 and out of scope; note that `active_version` history
  is preserved enough to enable it later (rollback = a *forward* re-apply of an older config as a new
  version). (`APLY-29..30`)

---

## Specific References

- **AD-005** (atomic config swap via double-buffer `active_slot`; rollback = flip back) — the data-plane
  mechanism the `applying → active` transition ultimately drives (M4/M2); this feature only tracks the
  status, it does not swap slots.
- **A-SRL-3** (service-rule-list stops at `pending` and owns the `version` bump) — the handoff this
  feature picks up; the enqueue reads the `version` this feature does **not** own.
- **TDD 4.5** (config propagation & apply-status sequence: DB pending + audit → enqueue [queued] → 202 →
  worker applying → active/failed), **TDD 4.6** (202 response example: `{apply_status, version,
  active_version}`), **TDD 9.1/9.2** (admin panel surfaces map active version + apply status + worker job
  backlog), **AD-008** (async stack: `redis.asyncio`, `compose.test.yml` PG+Redis).
- **PRD 8.1/8.3** (config propagation atomic via one `active_slot`; ≤5 s target), **OP-05** (one-click
  rollback — GA), **CM-01** (no HA — unrelated but the reason `failed` must keep the prior active live).

---

## Deferred Ideas

- Explicit batch **Apply/Publish** gate (review-before-apply) instead of auto-enqueue — a relaxation of
  D-APLY-1 (Backlog/GA).
- Global-blacklist and threat-feed apply-status targets (generic `ApplyTarget`) — arrives with M4.
- One-click **rollback to previous version** (OP-05, GA/M7).
- **Job cancellation** of a superseded in-flight apply (v1 relies on the version guard instead).
- Alerting on `apply_status=failed` / worker backlog (M6 *Alerting*).
