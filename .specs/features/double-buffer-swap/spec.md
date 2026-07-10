# Double-buffer Map Build/Swap Specification

**Feature:** M4 #2 — Double-buffer map build/swap
**Context:** `.specs/features/double-buffer-swap/context.md` (D-DBS-1..3, A-DBS-1..8)
**Status:** Draft (awaiting approval → Design)
**Depends on:** agent-worker **executed** (M4 #1; currently Tasks APPROVED) — provides the `Applier`
boundary, `HANDLERS` registry, and `process_job` two-transaction guard this feature plugs into.
Reuses the frozen M4 build contracts (AD-015/019/021/023/025) and the loader/pin pattern (AD-017).

---

## Problem Statement

The agent-worker (M4 #1) drives every committed config change through `queued → applying → active`,
but its v1 applier is a **placeholder** that only logs and succeeds (D-AGW-1) — `active` means
"acknowledged by the worker", and the XDP data-plane is still driven solely by the loader's env seed
(D-SLRD-1 interim writer). A tenant/admin change is durably tracked but **never enforced**. This
feature is the authoritative writer: it builds the inactive slot of the data-plane's double-buffered
config maps from PostgreSQL, verifies it structurally, and atomically flips `active_config.active_slot`
so the change actually reaches the hot path — with fail-closed rollback that keeps the last-good slot
live on any failure.

## Goals

- [ ] A committed `SERVICE_UPDATE` **reaches the active XDP slot** (real enforcement), not just an
      `active_version` bump — re-validating the ≤5 s propagation bound with real map builds (A-AGW-7).
- [ ] Every apply builds a **full inactive slot**, **structurally verifies** it, and commits with a
      **single `active_config` write**; a failed build/verify **never flips** (last-good slot stays live).
- [ ] Only **slotted config maps** are rebuilt/swapped; **unslotted runtime-state maps** (token buckets,
      counters) are never touched by the swap — §8.3.
- [ ] The agent-worker's applier **boundary and orchestration are unchanged** — the implementation is
      swapped behind the injection point, not the boundary (D-AGW-1).

## Out of Scope

| Feature | Reason |
| --- | --- |
| Authoritative content of the **global blacklist / blocked-port bitmap** | M4 #3 *Threat feed sync* owns it; this feature only **carries it forward** across a swap (D-DBS-2, A-DBS-4) |
| Worker loop / reconcile / orphan recovery | Owned by agent-worker (M4 #1, executed); this feature only replaces the applier impl |
| New `JobType`s (`MAP_REBUILD` / `ACTIVE_SLOT_SWAP`) | Full-node-rebuild-per-`SERVICE_UPDATE` subsumes them in v1 (A-DBS-1); deferred |
| **Incremental** slot copy (clone-active-then-overwrite-one-service) | D-DBS-2 chose full-node rebuild; incremental deferred |
| **Live-probe** (`BPF_PROG_TEST_RUN`) verification | D-DBS-3 chose structural read-back; live probe deferred |
| Bypass / maintenance-mode use of `active_config` | M6 |
| Telemetry aggregation of slot/version | M5 (this feature only exposes the raw values via dpstat) |
| User-facing "roll back to version K" surface (OP-05) | GA; this feature ships only the flip-back mechanism |
| New API endpoints | M1 read surfaces + `GET /jobs` already show everything |

---

## User Stories

### P1: Authoritative double-buffer applier ⭐ MVP

**User Story**: As the gateway operator, I want a committed service change to be built into the data-plane
and atomically activated, so that the config the control-plane reports as `active` is the config the XDP
hot path actually enforces.

**Why P1**: This is the whole point of M4 — closing the loop from control-plane commit to data-plane
enforcement. Without it, `active` is a lie (D-AGW-1).

**Acceptance Criteria**:

1. WHEN a `SERVICE_UPDATE` job reaches `handle_service_update` THEN the worker SHALL invoke the
   `DoubleBufferApplier` behind the **unchanged** `Applier` boundary (placeholder swapped at the
   injection site only). **(DBS-01)**
2. WHEN an apply runs THEN the applier SHALL build the **inactive** slot (`1 − active_config.active_slot`)
   of every slotted config map and SHALL NOT mutate the live slot. **(DBS-02)**
3. WHEN the inactive slot is built and verified THEN the applier SHALL commit with a **single**
   `active_config` write that sets `active_slot` to the newly built slot. **(DBS-03)**
4. WHEN the swap succeeds THEN the executed `mark_active` SHALL advance the triggering service's
   `active_version` to N (state machine per-service; slot is the physical carrier). **(DBS-04)**
5. WHEN an apply runs THEN the applier SHALL NOT touch any **unslotted** runtime-state map (token
   buckets, counters, sample/bloom stats) — §8.3. **(DBS-05)**

**Independent Test**: Enqueue a `SERVICE_UPDATE` via the executed service path; after the worker
processes it, assert `active_config.active_slot` flipped and a `BPF_PROG_TEST_RUN` (or gated two-veth
smoke) shows the hot path enforcing the newly-built config (e.g. a newly-added allow-rule now admits).

---

### P1: C apply-helper + pinned config maps

**User Story**: As the worker, I want to delegate map build/verify/swap to a C helper that reuses the
loader's proven BPF routines, so that all kernel-map manipulation lives in one audited surface instead
of being re-implemented across the Python boundary.

**Why P1**: The mechanism the whole feature stands on (D-DBS-1); fresh inner-map creation for
replace-only blooms + LPM tries is load-bearing C.

**Acceptance Criteria**:

1. WHEN `DoubleBufferApplier.apply(config)` runs THEN it SHALL invoke a **C helper binary** (subprocess)
   that performs build + verify + swap, reusing the loader's `seed_*` / inner-map-creation routines.
   **(DBS-06)**
2. WHEN the helper exits **0** THEN the applier SHALL treat the apply as succeeded (→ `mark_active`);
   WHEN it exits **nonzero** THEN the applier SHALL raise, mapping to `mark_failed(err)` with the
   helper's stderr captured. **(DBS-07)**
3. WHEN the data-plane is loaded THEN the loader SHALL **pin** all slotted config maps + `active_config`
   under the bpffs pin dir so the separate helper process can open them by pin (additive to the existing
   observability pins). **(DBS-08)**
4. WHEN building the inactive slot THEN the helper SHALL **create fresh inner maps** (bloom / LPM / vip /
   fair / bitmap) and install them into each `ARRAY_OF_MAPS` outer at the inactive index (blooms are
   replace-only — no in-place clear). **(DBS-09)**
5. WHEN building THEN the helper SHALL receive the **full node config** (all active services + plan +
   rules + whitelist + blacklist); the input channel (worker-serialized snapshot vs helper reads PG) is
   fixed at Design (A-DBS-2). **(DBS-10)**

**Independent Test**: Run the helper standalone against a loaded (or `BPF_PROG_TEST_RUN`) data-plane with
a fixture node config; assert the inactive slot is populated, inner maps installed, `active_config`
flipped, exit 0; corrupt the fixture → exit nonzero, no flip.

---

### P1: Fail-closed rollback

**User Story**: As the operator, I want a failed build or verify to leave the previous config live and
untouched, so that a bad apply degrades to "no change" rather than breaking enforcement.

**Why P1**: PRD 6.8 "swap-only-on-full-build"; fail-closed inline is the safety contract of the whole
gateway.

**Acceptance Criteria**:

1. WHEN any build write (`bpf_map_update_elem`, inner-map create/install) fails THEN the helper SHALL
   abort **without** flipping `active_config`; the last-good slot stays live. **(DBS-11)**
2. WHEN structural verify fails THEN the helper SHALL abort without flipping; the applier raises →
   `mark_failed`, and the triggering service's `active_version` is **kept** (executed guarantee). **(DBS-12)**
3. A **partially built** inactive slot SHALL never become live — only a **passed verify** triggers the
   flip (rollback is "abort before flip", never "flip then flip back"). **(DBS-13)**
4. WHEN the helper is killed/crashes **before** the flip THEN no swap SHALL have occurred and the live
   slot SHALL be unchanged (crash-consistent; the half-built inactive slot is overwritten by the next
   apply). **(DBS-14)**

**Independent Test**: Inject a build failure (unwritable map / oversized entry) and a verify mismatch;
assert exit nonzero, `active_slot` unchanged, and the hot path still enforces the prior config; kill the
helper mid-build → `active_slot` unchanged.

---

### P1: Full-node rebuild with feed-map carry-forward

**User Story**: As the operator, I want every apply to rebuild the whole node's service config into the
inactive slot while preserving the global blacklist, so that a single-service change produces a complete,
self-consistent slot and never accidentally drops node-wide deny state.

**Why P1**: Per-service jobs drive a node-global flip (D-DBS-2); consistency of the flipped slot is a
correctness requirement, not an optimization.

**Acceptance Criteria**:

1. WHEN an apply runs THEN the helper SHALL rebuild **all active services'** service-scoped slotted maps
   (`service_map`, `rule_block`, `whitelist`/`vip`, `service_blacklist`, `fair_config`) into the inactive
   slot from current DB config. **(DBS-15)**
2. WHEN a service is disabled or deleted THEN the rebuilt slot SHALL reflect it (absent / disabled), and
   both slots SHALL converge over successive applies. **(DBS-16)**
3. WHEN an apply runs THEN the **feed-owned global deny maps** (`global_blacklist_bloom`/`lpm`,
   `udp_blocked_port_bitmap`, `gbl_meta`) SHALL be **carried forward** into the new slot so a per-service
   swap never drops the global blacklist (M4 #3 owns their content). **(DBS-17)**
4. WHEN the build finishes THEN structural verify SHALL confirm every **enabled** service is present in
   `service_map[inactive]` with matching `rule_block` version/count and all per-slot inner maps installed,
   **before** the flip (D-DBS-3). **(DBS-18)**

**Independent Test**: With services A, B, C active and a seeded global blacklist, apply a change to A;
assert the new slot enforces A's change **and** B/C **and** the global blacklist unchanged; delete B and
apply → B absent in the new slot, A/C/global intact.

---

### P2: Version stamping & idempotency

**User Story**: As the operator, I want each successful swap to bump a monotonic node map version and
re-applies to be safe, so that slot state is observable and churn/duplicate delivery can't tear a swap.

**Why P2**: Observability + concurrency safety; the core swap works without it, but v1 reliability needs it.

**Acceptance Criteria**:

1. WHEN a swap succeeds THEN `active_config.version` SHALL increment monotonically (node-global map
   version, distinct from each service's per-service `active_version`). **(DBS-19)**
2. WHEN a job is superseded (`service.version` moved during apply) THEN the agent-worker's two-transaction
   guard SHALL skip the terminal advance and NO torn/partial flip SHALL result (either the whole new slot
   is live or it is not). **(DBS-20)**
3. WHEN an already-applied version is re-applied THEN the rebuild SHALL be **idempotent** (identical slot
   contents; a flip or a no-op, never corruption). **(DBS-21)**

**Independent Test**: Concurrently commit `version=N+1` while `apply(N)` is mid-build (BarrierApplier
style); assert exactly one advance and one final live slot; re-run `apply(N)` → identical slot, no error.

---

### P2: Startup coherence & restart preservation

**User Story**: As the operator, I want worker/data-plane restarts to preserve the active slot and never
trigger an unsolicited swap, so that a restart is invisible to traffic.

**Why P2**: PRD 11.3 (restart must not lose active state); binds AGW-21 onto the real applier.

**Acceptance Criteria**:

1. WHEN the worker starts THEN the applier SHALL NOT perform an unsolicited swap; only a job drives a
   swap (AGW-21 binding). **(DBS-22)**
2. WHEN the data-plane is (re)loaded THEN the loader seed SHALL establish an initial coherent slot, and
   the **first** successful apply SHALL reconcile the live slot to true DB state. **(DBS-23)**
3. WHEN the worker restarts THEN the current `active_config.{active_slot, version}` SHALL be read and
   preserved; the helper builds the **other** slot (PRD 11.3). **(DBS-24)**

**Independent Test**: Load + seed, restart the worker, apply a change; assert the pre-restart slot was
never flipped by the restart itself and the post-restart apply builds the opposite slot.

---

### P2: ≤5 s propagation with real builds

**User Story**: As the operator, I want a committed change to reach the active slot within 5 s even with
real map builds at the pilot scale envelope, so that the config-propagation SLA holds end-to-end.

**Why P2**: A-AGW-7 re-validation; the number must survive real builds, not just the placeholder.

**Acceptance Criteria**:

1. WHEN a service change commits THEN it SHALL reach the active data-plane slot **≤ 5 s** nominal
   (measured end-to-end: commit → job → build → verify → flip). **(DBS-25)**
2. WHEN the node holds the scale envelope (≤ 1000 services × ≤ 16 rules) THEN the full-node rebuild cost
   SHALL be **measured** at a gated check to fit the ≤ 5 s budget, with documented mitigation if it does
   not (carry-forward already excludes the 1M global blacklist from per-job rebuild). **(DBS-26)**

**Independent Test**: Time a single apply on a small config (assert ≤ 5 s); a gated bulk test seeds the
1000-service envelope and reports the rebuild+swap wall time.

---

### P3: Slot/version observability via dpstat

**User Story**: As the operator, I want to see which slot is live and at what version, so that I can
confirm a swap happened and debug a stuck apply.

**Why P3**: Nice-to-have operator surface; M5 telemetry consumes the same values later.

**Acceptance Criteria**:

1. WHEN `dpstat` is run THEN it SHALL surface `active_config.active_slot`, `active_config.version`, and
   (if recorded) a last-swap timestamp. **(DBS-27)**
2. WHEN a swap occurs THEN the worker SHALL emit a structured log line (service_id, version, slot
   old→new, verify result, duration_ms). **(DBS-28)**

---

## Edge Cases

- WHEN the triggering service is CASCADE-deleted mid-flight THEN the rebuild SHALL reflect its absence
  and the two-txn guard SHALL supersede the job (no crash). **(→ DBS-16, DBS-20)**
- WHEN a fresh inner-map create fails (e.g. ENOMEM installing a bloom/LPM inner) THEN the build SHALL
  abort with no flip. **(→ DBS-09, DBS-11)**
- WHEN the `active_config` write itself fails after a successful build+verify THEN the last-good slot
  SHALL stay live and the apply SHALL fail (retry-able); no half-flip is possible (single-key write).
  **(→ DBS-03, DBS-11)**
- WHEN the node snapshot handed to the helper is truncated/invalid THEN verify (or an input schema check)
  SHALL fail before any flip. **(→ DBS-10, DBS-18)**
- WHEN two applies race (unsupported stray second writer) THEN the state-machine guard keeps ledger state
  safe, but physical slot integrity assumes a single writer (A-DBS-8). **(→ DBS-20)**
- WHEN the global deny maps are empty (no seed, no feed yet) THEN carry-forward SHALL be a no-op and the
  new slot SHALL have empty global deny maps (baseline). **(→ DBS-17)**

---

## Requirement Traceability

| Requirement ID | Story | Phase | Status |
| --- | --- | --- | --- |
| DBS-01 | P1: Authoritative applier | Design | Pending |
| DBS-02 | P1: Authoritative applier | Design | Pending |
| DBS-03 | P1: Authoritative applier | Design | Pending |
| DBS-04 | P1: Authoritative applier | Design | Pending |
| DBS-05 | P1: Authoritative applier | Design | Pending |
| DBS-06 | P1: C apply-helper | Design | Pending |
| DBS-07 | P1: C apply-helper | Design | Pending |
| DBS-08 | P1: C apply-helper | Design | Pending |
| DBS-09 | P1: C apply-helper | Design | Pending |
| DBS-10 | P1: C apply-helper | Design | Pending |
| DBS-11 | P1: Fail-closed rollback | Design | Pending |
| DBS-12 | P1: Fail-closed rollback | Design | Pending |
| DBS-13 | P1: Fail-closed rollback | Design | Pending |
| DBS-14 | P1: Fail-closed rollback | Design | Pending |
| DBS-15 | P1: Full-node rebuild | Design | Pending |
| DBS-16 | P1: Full-node rebuild | Design | Pending |
| DBS-17 | P1: Full-node rebuild | Design | Pending |
| DBS-18 | P1: Full-node rebuild | Design | Pending |
| DBS-19 | P2: Version & idempotency | Design | Pending |
| DBS-20 | P2: Version & idempotency | Design | Pending |
| DBS-21 | P2: Version & idempotency | Design | Pending |
| DBS-22 | P2: Startup coherence | Design | Pending |
| DBS-23 | P2: Startup coherence | Design | Pending |
| DBS-24 | P2: Startup coherence | Design | Pending |
| DBS-25 | P2: ≤5 s propagation | Design | Pending |
| DBS-26 | P2: ≤5 s propagation | Design | Pending |
| DBS-27 | P3: Observability | Design | Pending |
| DBS-28 | P3: Observability | Design | Pending |

**ID format:** `DBS-[NUMBER]`
**Status values:** Pending → In Design → In Tasks → Implementing → Verified
**Coverage:** 28 total, 0 mapped to tasks (Design/Tasks pending).

---

## Success Criteria

- [ ] A committed `SERVICE_UPDATE` flips `active_config.active_slot` and the XDP hot path enforces the new
      config (verified by `BPF_PROG_TEST_RUN` + gated two-veth smoke).
- [ ] A forced build/verify failure leaves the prior slot live and the service `failed` with
      `active_version` unchanged.
- [ ] Full-node rebuild preserves the global blacklist and all non-triggering services across a
      per-service swap.
- [ ] End-to-end commit→active ≤ 5 s on a nominal config; the scale-envelope rebuild cost is measured.
- [ ] The agent-worker's `apply.py`, `process_job`, and the `Applier` boundary are **byte-for-byte
      unchanged** except the injection-site implementation swap and additive settings.
- [ ] `dpstat` reports the live slot + version.
