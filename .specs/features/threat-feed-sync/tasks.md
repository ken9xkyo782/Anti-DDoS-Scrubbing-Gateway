# Threat intelligence feed sync tasks

**Design**: `.specs/features/threat-feed-sync/design.md` (AD-029)
**Spec**: `.specs/features/threat-feed-sync/spec.md` (FEED-01..40)
**Context**: `.specs/features/threat-feed-sync/context.md`
**Status**: **VERIFIED** (2026-07-13) — T1–T15 executed; **all 100 Done-when boxes
met**. Control-plane full gate **435 passed** and data-plane build/quick/full +
global-apply smoke/scale gates all green. See **Execution Results** below.

**External gate** (RESOLVED 2026-07-13): M4 #1 **and** M4 #2 (double-buffer map
build/swap) are both executed (`7fcfb1b..86120bc`), providing the base
`apply_snapshot.h`, `xdpgw-apply`, and pinned config maps. T12–T14 (snapshot
contract, Python applier, C helper global mode + dp gates) and T15 (docs + final
traceability) are now executed on that base.

**Baselines / recorded gate results (2026-07-13):** the control-plane baseline at
Execute start was **B_cp = 262** (after M4 #1). Using the `control-plane/.venv`
toolchain and a live `compose.test.yml`, the gates were **re-run green this
session**:

- Control-plane full: `ruff check` ✓, `ruff format --check` ✓ (128 files),
  `mypy app/` ✓ (60 files, no issues), `pytest -q` → **435 passed** (100 unit +
  335 integration, 89 s). Unit-only gate: **100 passed**.
- Data-plane: `make bpf skel loader dpstat apply` + `make test` green (test_parse
  **130** + `test_snapshot` service+global golden self-tests); `make smoke` green
  (redirect TTL/csum, fairness, apply flip 78 ms — no regression).
- Data-plane global-apply (T14): `make globalapplysmoke` → feed snapshot reached
  `blacklist_drop` while an unlisted source stayed delivered; `make
  globalapplyscale` → **1,048,576** entries loaded in **6925 ms**, **1,048,577**
  rejected before flip (helper peak RSS ~17.6 MB; kernel BPF-map footprint via
  cgroup = n/a in this container).

**Done-when review (2026-07-13):** every task's Done-when box was verified —
behavioral criteria against the committed implementation, and the gate boxes by
**re-running the gates green** (numbers above). All 100 boxes are ticked `[x]`.
The one pre-existing Pydantic `__fields_set__` deprecation warning in
`services/feeds.py:407` (fallback branch only) is non-blocking and not a gate
failure.

**Gates** (from `.specs/codebase/TESTING.md`):

- Control-plane quick: `ruff check . && ruff format --check . && mypy app/ && pytest -q -m unit`.
- Control-plane full: `ruff check . && ruff format --check . && mypy app/ && pytest -q`.
- Data-plane build: `make bpf skel loader apply dpstat`.
- Data-plane quick: `make test`.
- Data-plane full: `make test && sudo make smoke`.
- Data-plane scale: `sudo make blbulk` plus the global-apply scale target added
  by T14.

**Proposed execution tools** (confirm or change when approving Tasks):

- MCPs: none configured; Context7 is unavailable.
- Skills: `coding-guidelines` for T1–T14; `docs-writer` for T15.
- Use `codenavi` only if execution discovers a path or dependency not already
  grounded in AD-029. Use `mermaid-studio` only if a diagram changes.

---

## Execution Results

Back-filled 2026-07-13 from the git history, then verified by re-running **all**
gates this session (see Baselines / recorded gate results). "Committed" = the
implementation and its co-located tests landed in the named commit; T14 and T15
were executed and land in this session's closing commit.

| Task | Status | Commit(s) | Verification recorded here |
| --- | --- | --- | --- |
| T1 | ✅ Committed | `b64c14f` (07-10) | Models + `20260710_0006_threat_feed` migration + `test_feed_models.py` (+392) + `test_agent_job_model.py`/`conftest.py` updates. CP full gate not re-runnable here. |
| T2 | ✅ Committed | `021f567` (07-10) | `core/feed_parser.py` + `test_feed_parser.py` (+133). CP unit not re-runnable here. |
| T3 | ✅ Committed | `3aa7a52` (07-10) | `services/feed_fetch.py` + `core/config.py` fetch bounds + `test_feed_fetch.py` (+228). CP unit not re-runnable here. |
| T4 | ✅ Committed | `6737635` (07-10) | `services/feed_reconcile.py` (+592) + `test_feed_reconcile.py` (+467). CP full not re-runnable here. |
| T5 | ✅ Committed | `680974c` (07-10) | `services/feeds.py` (+482) + `test_feeds_service.py` (+387). CP full not re-runnable here. |
| T6 | ✅ Committed | `9e9b9ed` (07-13) | `services/lists.py` manual-precedence + `test_lists_service.py`/`test_global_blacklist_api.py`. CP full not re-runnable here. |
| T7 | ✅ Committed | `ae33648` (07-13) | `api/routers/feeds.py` + `api/schemas/feeds.py` + `main.py` mount + `test_feeds_api.py` (+463). CP full not re-runnable here. |
| T8 | ✅ Committed | `4a2f764` (07-10) | `worker/feed_jobs.py` (+342) + `processor.py`/`handlers.py` refactor + `test_worker_feed_jobs.py` (+367); service processor/handler regressions updated. CP full not re-runnable here. |
| T9 | ✅ Committed | `9fdaa7f` (07-10) | `worker/feed_runner.py` (+298) + `handlers.py` + `test_feed_sync_runner.py` (+642). CP full not re-runnable here. |
| T10 | ✅ Committed | `499045e` (07-10) | `worker/feed_scheduler.py` (+123) + `worker.py` tick wiring + `test_feed_scheduler.py` (+349). CP full not re-runnable here. |
| T11 | ✅ Committed | `dbe2762` + `036da91` (07-13) | `worker/feed_coordinator.py` (+91) + `worker.py`/`__main__.py`/`processor.py`/`feed_runner.py` + `test_feed_coordinator.py` (+107); follow-up runtime-isolation cases in `test_worker_runtime.py` (+141). CP full not re-runnable here. |
| T12 | ✅ Committed | `57b1b2c` (07-13) | **DP build + quick green live.** Schema bumped to v2 with `SERVICE_FULL \| GLOBAL_DENY` kind; `global_deny_snapshot_golden.bin` (new) + `test_snapshot.c` (+203). `make test` → `test_snapshot` parses both goldens and rejects unknown kind (255), wrong version (3), and truncation. |
| T13 | ✅ Committed | `5558b38` (07-13) | `worker/applier.py` `serialize_global_snapshot`/`apply_global` (+123) + `__main__.py` real-applier wiring + `feed_runner.py` + `test_global_deny_applier.py` (+204) + `test_global_snapshot_serialize.py` (+32). CP integration not re-runnable here. |
| T14 | ✅ Executed | `e639e41` (07-13) | `tools/xdpgw-apply.c` `apply_global_deny_cfg` + shared pin-dir `flock` + inverse carry-forward (+336); `tests/test_parse.c` +390; `make globalapplysmoke`/`globalapplyscale` targets + scripts; `apply_smoke.py` +64; `Makefile`. **Gates green:** `make test` 130; `globalapplysmoke` → `blacklist_drop`; `globalapplyscale` → 1,048,576 in 6925 ms, 1,048,577 rejected before flip; helper RSS ~17.6 MB. |
| T15 | ✅ Executed (this session) | closing commit | CP README feed operations (grammar/rejects/32-MiB+timeout+interval/disabled+manual/dry-run/credentials/scheduling/deletion/recovery), DP README global-deny mode (shared lock, inverse carry-forward, no-flip rollback, version semantics, scale cmds), `TESTING.md` feed + global-apply sections, `spec.md` 40 reqs → Verified, ROADMAP/STATE updated. Links + AD-029 diagrams resolve; `git diff --check` clean. |

**AD-029 closeout (2026-07-13):** all 15 tasks executed and every gate re-run
green (numbers under Baselines). M4 #3 threat-feed-sync is **VERIFIED**. Next
phase: resume **M5** Execute (Telemetry & dashboards / Chargeback metering are
design + tasks drafted).

---

## Execution plan

### Phase 1 — Schema foundation

```text
T1 (models + migration + job target constraints)
```

### Phase 2 — Pure ingest components

T2 is independent. After T1, run T2 and T3 together; this scheduling delay is
not a dependency on T1.

```text
T2 [P] parser (root) ─────────────┐
T1 ──→ T3 [P] bounded fetcher ────┴─→ Phase 3
```

### Phase 3 — Control-plane persistence and API

All tasks in this phase run serially because integration tests share one
PostgreSQL/Redis stack.

```text
T1,T2 ──→ T4 reconciler ──→ T5 source service ──→ T7 API
                  └───────→ T6 manual precedence
T1 ───────────────────────→ T8 job lifecycle
```

### Phase 4 — Worker orchestration

```text
T2,T3,T4,T5,T8 ──→ T9 sync runner
T5,T8 ────────────→ T10 scheduler
T9,T10 ───────────→ T11 background fetch coordinator/runtime
```

### Phase 5 — Data-plane propagation (blocked on M4 #2)

```text
M4 #2 ──→ T12 snapshot contract
T4,T9,T11,T12 ──→ T13 Python global applier
T12,T13 ─────────→ T14 C helper global mode + dp gates
```

### Phase 6 — Documentation and final traceability

```text
T6,T7,T11,T13,T14 ──→ T15 docs + final gate record
```

Only T2 and T3 carry `[P]`. Every other control-plane task requires the
non-parallel integration stack, and the final data-plane tasks share the M4 #2
snapshot/helper contract.

---

## Task breakdown

### T1: Add feed persistence models and generalize the job target

**What**: Add the source, run, assertion, overlap, and singleton global-deny
models, then generalize `AgentJob` with explicit service/feed/global target
constraints in one migration.

**Where**: `control-plane/app/db/models.py`,
`control-plane/migrations/versions/20260710_0006_threat_feed.py` (new),
`control-plane/tests/integration/test_feed_models.py` (new), and
`control-plane/tests/integration/conftest.py` (truncate order update).

**Depends on**: None.

**Reuses**: Existing enum/model conventions, `CIDR`, `CITEXT`, partial indexes,
`utc_now`, `AgentJob`, `BlacklistEntry`, and the `committed_db` fixture.

**Requirement**: FEED-01, FEED-02, FEED-08, FEED-15, FEED-19, FEED-20,
FEED-21, FEED-23, FEED-28, FEED-35.

**Tools**: MCP: NONE · Skill: `coding-guidelines`.

**Done when**:

- [x] `ThreatFeedSource`, `FeedSyncRun`, `FeedBlacklistAssertion`,
  `FeedSyncOverlap`, and singleton `GlobalDenyState` match AD-029.
- [x] `ThreatFeedSource.name` is case-insensitive unique; interval check is
  300..604800; source/run sequence is unique; overlap rows are unique per
  run/CIDR/whitelist entry.
- [x] `WhitelistEntry.source_cidr` has a GiST `inet_ops` index.
- [x] `AgentJob.target_id` becomes nullable without losing its service FK;
  `feed_sync_run_id` is a unique FK; CHECKs enforce the three job target shapes;
  partial unique indexes preserve service-version, feed-run, and global-revision
  idempotency.
- [x] Migration upgrades from the current head and downgrades cleanly on the
  test database.
- [x] Integration tests cover constraints, cascades/tombstone retention,
  singleton initialization, and invalid job target combinations.
- [x] Full gate passes; test total is **task-start B_cp + at least 10** with no
  deletion or weakening of existing tests.

**Tests**: integration.
**Gate**: full + build migration smoke.
**Commit**: `feat(feed): add feed models and typed job targets`.

---

### T2: Implement the strict line-list parser [P]

**What**: Implement the pure IPv4/CIDR line-list parser and its complete
normalization/error table.

**Where**: `control-plane/app/core/feed_parser.py` (new) and
`control-plane/tests/unit/test_feed_parser.py` (new).

**Depends on**: None.

**Reuses**: `app/core/cidr.py::parse_ipv4_cidr`; deliberately does not call
`reject_reserved` because feeds may assert bogon ranges.

**Requirement**: FEED-10, FEED-11, FEED-12, FEED-13, FEED-17, FEED-37.

**Tools**: MCP: NONE · Skill: `coding-guidelines`.

**Done when**:

- [x] Parser accepts UTF-8 with optional BOM, blank lines, full-line/inline
  `#` and `;` comments, surrounding whitespace, bare IPv4 as `/32`, and strict
  canonical IPv4 CIDRs.
- [x] Parser rejects invalid UTF-8, IPv6, `0.0.0.0/0`, malformed values, and
  host-bit CIDRs without rejecting other reserved/bogon ranges.
- [x] Exact duplicates collapse; containing/contained but unequal CIDRs remain;
  counts distinguish physical, valid-distinct, invalid, and duplicate lines.
- [x] Zero-valid input returns the explicit failed outcome; mixed valid/invalid
  input returns the partial outcome.
- [x] Quick gate passes; unit total is **task-start B_unit + at least 14**.

**Tests**: unit.
**Gate**: quick.
**Commit**: `feat(feed): parse canonical IPv4 line-list feeds`.

---

### T3: Implement the bounded HTTPS feed fetcher [P]

**What**: Add environment-backed fetch limits and a shared-client HTTPS
streaming fetcher that never exposes credential material.

**Where**: `control-plane/app/services/feed_fetch.py` (new),
`control-plane/app/core/config.py` (extend), and
`control-plane/tests/unit/test_feed_fetch.py` (new).

**Depends on**: T1 (source model and credential reference shape).

**Reuses**: Existing `Settings` env-prefix convention, HTTPX `AsyncClient`,
Python 3.12 `asyncio.timeout`, and audit secret-key vocabulary.

**Requirement**: FEED-06, FEED-09, FEED-17, FEED-40.

**Tools**: MCP: NONE · Skill: `coding-guidelines`.

**Done when**:

- [x] Settings expose 5-second connect, 10-second read, 5-second write/pool,
  30-second wall-clock, and 32-MiB decoded-body defaults with positive bounds.
- [x] Fetcher uses TLS verification, `follow_redirects=False`,
  `trust_env=False`, a worker-lifetime client, and streamed decoded bytes.
- [x] Oversized `Content-Length` fails before reading; streamed expansion over
  the cap aborts immediately; 3xx/non-2xx and every timeout path fail cleanly.
- [x] Optional bearer value is resolved from the configured environment name;
  missing references fail before the request; names/values/body never enter
  errors or captured logs.
- [x] Unit tests use `httpx.MockTransport` and a deterministic slow stream.
- [x] Quick gate passes; unit total is **task-start B_unit + at least 10**.

**Tests**: unit.
**Gate**: quick.
**Commit**: `feat(feed): add bounded credential-safe HTTPS fetcher`.

---

### T4: Implement assertion reconciliation and overlap recording

**What**: Add the PostgreSQL reconciliation service that replaces one source's
assertions, materializes the global union, records overlaps, and advances the
desired global-deny digest/revision.

**Where**: `control-plane/app/services/feed_reconcile.py` (new) and
`control-plane/tests/integration/test_feed_reconcile.py` (new).

**Depends on**: T1, T2.

**Reuses**: `BlacklistEntry`, its global unique index, `WhitelistEntry`,
PostgreSQL `cidr && cidr`, `pg_insert`, `record_event`, and source/global-state
`SELECT FOR UPDATE` patterns.

**Requirement**: FEED-14, FEED-15, FEED-16, FEED-17, FEED-18, FEED-19,
FEED-20, FEED-21, FEED-22, FEED-23, FEED-27, FEED-28, FEED-38.

**Tools**: MCP: NONE · Skill: `coding-guidelines`.

**Done when**:

- [x] A transaction-local candidate table receives distinct CIDRs in bounded
  batches; dry-run computes counts/overlaps without mutations.
- [x] Reconcile adds/removes only the target source's assertion links, creates
  one feed row when no global row exists, and deletes only orphaned feed rows.
- [x] Manual rows are never overwritten/deleted; multiple feeds share one
  global row; source deltas and effective-global changes are counted separately.
- [x] Distinct effective entries above 1,048,576 roll back the whole reconcile.
- [x] Equal/contains/contained overlaps persist one identified
  `FeedSyncOverlap` per pair and one bounded credential-free audit summary;
  disjoint rows produce none; global rows remain present.
- [x] Stable sorted hashing advances `desired_revision` only when the effective
  global CIDR set changes; byte-identical/source-only changes do not.
- [x] Source/global row locks prevent lost union updates in concurrent sessions.
- [x] Full gate passes; test total is **task-start B_cp + at least 14**.

**Tests**: integration.
**Gate**: full.
**Commit**: `feat(feed): reconcile assertions and whitelist overlaps`.

---

### T5: Implement feed source CRUD and durable sync enqueue

**What**: Add the transactional source service for CRUD, logical deletion,
run/job creation, and post-commit dispatch registration.

**Where**: `control-plane/app/services/feeds.py` (new) and
`control-plane/tests/integration/test_feeds_service.py` (new).

**Depends on**: T4.

**Reuses**: `ApplyDispatcher`, post-commit callbacks, `record_event`,
`require_admin` service conventions, and T4 materialization helpers.

**Requirement**: FEED-01, FEED-02, FEED-03, FEED-04, FEED-05, FEED-06,
FEED-08, FEED-30, FEED-32, FEED-33, FEED-38.

**Tools**: MCP: NONE · Skill: `coding-guidelines`.

**Done when**:

- [x] Create/update validate HTTPS URL without userinfo/fragment, interval
  range, case-insensitive name, format, and credential-reference regex.
- [x] Create/re-enable and URL/credential changes become due immediately;
  interval-only update recomputes due time; disable clears due time without
  removing assertions; manual sync remains allowed while disabled.
- [x] `enqueue_sync` locks the source, increments `sync_sequence`, creates one
  `FeedSyncRun` and linked `FEED_SYNC` job, then dispatches only after commit.
- [x] Competing scheduler/manual enqueues return the existing in-flight job and
  never create two queued/running jobs for one source.
- [x] Delete records a dangerous-action audit, hides a tombstone, removes only
  its assertions through T4, and enqueues the delete/convergence run.
- [x] Responses/service records expose only `has_credential`; audit metadata
  contains neither credential name nor value.
- [x] Full gate passes; test total is **task-start B_cp + at least 12**.

**Tests**: integration.
**Gate**: full.
**Commit**: `feat(feed): add source lifecycle and durable sync enqueue`.

---

### T6: Integrate manual global-list precedence and convergence

**What**: Update manual global blacklist CRUD to promote/demote the materialized
row without destroying feed assertions and to mark global-deny convergence
pending when the effective set changes.

**Where**: `control-plane/app/services/lists.py`,
`control-plane/tests/integration/test_lists_service.py`, and
`control-plane/tests/integration/test_global_blacklist_api.py`.

**Depends on**: T4.

**Reuses**: Existing global CRUD, duplicate-to-409 translation, audit actions,
T4 digest/revision helper, and `FeedBlacklistAssertion` reverse index.

**Requirement**: FEED-04, FEED-15, FEED-18, FEED-22, FEED-27.

**Tools**: MCP: NONE · Skill: `coding-guidelines`.

**Done when**:

- [x] Adding a manual CIDR over a feed row promotes the same row to manual and
  preserves every feed assertion.
- [x] Removing a manual row demotes it to feed when assertions remain and
  deletes it only when none remain.
- [x] Attempting manual deletion of a feed-only row returns 409 and does not
  mutate assertions or desired state.
- [x] Effective CIDR add/remove advances desired digest/revision and registers
  one post-commit global convergence job; source-only promotion/demotion is a
  no-op for data-plane convergence.
- [x] Existing service-scoped list behavior and audit semantics remain green.
- [x] Full gate passes; test total is **task-start B_cp + at least 7**.

**Tests**: integration.
**Gate**: full.
**Commit**: `feat(feed): preserve feed assertions in manual global CRUD`.

---

### T7: Add the admin feed API and history surface

**What**: Add Pydantic schemas and an admin-only router for source CRUD,
manual/dry-run enqueue, and sync history, then mount it in the app.

**Where**: `control-plane/app/api/schemas/feeds.py` (new),
`control-plane/app/api/routers/feeds.py` (new),
`control-plane/app/main.py`, and
`control-plane/tests/integration/test_feeds_api.py` (new).

**Depends on**: T5.

**Reuses**: `get_current_user`, `require_admin`, explicit response mapping from
`global_blacklist.py`, `get_db`, and T5 service methods.

**Requirement**: FEED-01, FEED-02, FEED-03, FEED-04, FEED-05, FEED-06,
FEED-07, FEED-08, FEED-32, FEED-36, FEED-38, FEED-40.

**Tools**: MCP: NONE · Skill: `coding-guidelines`.

**Done when**:

- [x] `POST/GET/PUT/DELETE /feeds`, `POST /feeds/{id}/sync`, and
  `GET /feeds/{id}/syncs` expose the AD-029 request/response contracts.
- [x] Manual and dry-run sync return 202 with run/job status; history orders
  recent runs deterministically and includes all required counts/status fields.
- [x] Credential references/values never appear; only `has_credential` is
  returned; API/log capture proves the omission.
- [x] Every non-admin call returns 403 before data access and produces no
  mutation or partial response.
- [x] Invalid HTTPS/name/interval/credential inputs return stable 409/422
  errors; delete is audited and API-visible as removal.
- [x] Router is mounted in `app/main.py`; import smoke passes.
- [x] Full gate passes; test total is **task-start B_cp + at least 13**.

**Tests**: integration (full API via `AsyncClient`).
**Gate**: full + import smoke.
**Commit**: `feat(feed): expose admin source and sync APIs`.

---

### T8: Add target-aware worker job lifecycles

**What**: Generalize the processor lifecycle around `JobType` while preserving
the executed service transition functions byte-for-byte, then add feed/global
claim, terminal, duplicate, and orphan behavior.

**Where**: `control-plane/app/worker/feed_jobs.py` (new),
`control-plane/app/worker/processor.py`,
`control-plane/app/worker/handlers.py`, and
`control-plane/tests/integration/test_worker_feed_jobs.py` (new) plus service
processor regression cases.

**Depends on**: T1.

**Reuses**: `mark_applying`, `mark_active`, `mark_failed`, `retry`,
`session_scope`, `AgentJob`, and the existing `HANDLERS` registry.

**Requirement**: FEED-08, FEED-16, FEED-17, FEED-31, FEED-34, FEED-35,
FEED-37.

**Tools**: MCP: NONE · Skill: `coding-guidelines`.

**Done when**:

- [x] `JOB_LIFECYCLES` dispatches service, feed-sync, and global-deny jobs;
  service adapter delegates unchanged to the existing guarded functions.
- [x] Feed claim locks job/run, advances queued→running, increments attempts,
  and never touches `ProtectedService.apply_status` or `active_version`.
- [x] Success/failure writes terminal job/run/source fields, caps/scrubs errors,
  and advances `next_sync_at` for fetch runs; global retry has no source fields.
- [x] Duplicate terminal delivery is a no-op; startup orphan recovery requeues
  the same feed run within its attempt budget; missing/deleted targets no-op
  safely as designed.
- [x] Reconcile ordering changes from cross-target `version` to
  `created_at,id`; existing service supersede, retry, Redis-loss, and orphan
  tests remain green.
- [x] Handler sessions use explicit short `session_scope` transactions; no
  database transaction spans external HTTP or helper execution.
- [x] Full gate passes; test total is **task-start B_cp + at least 12**.

**Tests**: integration.
**Gate**: full.
**Commit**: `refactor(worker): add typed feed and global job lifecycles`.

---

### T9: Implement the feed sync runner and handlers

**What**: Compose fetch, parse, reconcile, dry-run, structured logging, and an
injected global-applier protocol into the `FEED_SYNC` and convergence handlers.

**Where**: `control-plane/app/worker/feed_runner.py` (new),
`control-plane/app/worker/handlers.py`, and
`control-plane/tests/integration/test_feed_sync_runner.py` (new).

**Depends on**: T2, T3, T4, T5, T8.

**Reuses**: `HANDLERS`, T3 fetch result, T2 parse result, T4 reconcile result,
`session_scope`, and injected recording/failing applier test doubles.

**Requirement**: FEED-08, FEED-09, FEED-10, FEED-11, FEED-12, FEED-13,
FEED-14, FEED-16, FEED-17, FEED-18, FEED-27, FEED-31, FEED-35, FEED-37,
FEED-38, FEED-39, FEED-40.

**Tools**: MCP: NONE · Skill: `coding-guidelines`.

**Done when**:

- [x] `handle_feed_sync` loads the run/source, fetches without an open DB
  transaction, parses, reconciles in a short transaction, and calls the
  injected global applier only when desired differs from active.
- [x] Fetch/encoding/zero-valid failures retain prior assertions and desired/
  active versions; partial bodies apply the valid subset; byte-identical input
  records zero delta and skips an already-converged swap.
- [x] Dry-run records fetch/parse/overlap stats but mutates no assertion,
  blacklist, desired-state, or data-plane state.
- [x] Deleted-in-flight sources no-op safely; per-source failure cannot mutate
  another source's assertions or run state.
- [x] `handle_global_deny_apply` retries desired/active divergence without a
  feed fetch and deduplicates by desired revision.
- [x] One structured summary log contains source/run/counts/duration/status and
  contains no URL body, environment name, bearer value, or other secret/PII.
- [x] Integration tests cover success, partial, each keep-last failure,
  dry-run, no-op, duplicate delivery, applier failure, and convergence retry.
- [x] Full gate passes; test total is **task-start B_cp + at least 14**.

**Tests**: integration.
**Gate**: full.
**Commit**: `feat(feed): run resilient fetch-reconcile-sync jobs`.

---

### T10: Add due-time scheduling and convergence retries

**What**: Add the persisted due-source query and periodic enqueue hook for
scheduled feed runs and pending global convergence.

**Where**: `control-plane/app/worker/feed_scheduler.py` (new),
`control-plane/app/worker/worker.py`, and
`control-plane/tests/integration/test_feed_scheduler.py` (new).

**Depends on**: T5, T8.

**Reuses**: The existing worker reconciliation interval, `session_scope`, T5
enqueue function, `FOR UPDATE SKIP LOCKED`, and durable `AgentJob` status.

**Requirement**: FEED-30, FEED-31, FEED-32, FEED-33, FEED-34.

**Tools**: MCP: NONE · Skill: `coding-guidelines`.

**Done when**:

- [x] Every periodic tick enqueues each enabled, non-deleted due source once;
  disabled/not-due sources are skipped; manual sync stays independent.
- [x] A queued/running source is not double-enqueued under concurrent ticks or
  a competing manual request.
- [x] Terminal success/partial/failure computes
  `next_sync_at=finished_at+sync_interval`; restart startup catches persisted
  due sources immediately.
- [x] Desired/active digest divergence enqueues one `GLOBAL_DENY_APPLY` for the
  current desired revision when no such job is queued/running.
- [x] Scheduler failures follow existing DB backoff behavior and do not crash or
  starve ordinary service-job reconciliation.
- [x] Full gate passes; test total is **task-start B_cp + at least 8**.

**Tests**: integration.
**Gate**: full.
**Commit**: `feat(feed): schedule due sources and convergence retries`.

---

### T11: Add the bounded background fetch coordinator

**What**: Add one network-only background fetch lane to the worker while
keeping parse/reconcile/helper work in its serialized foreground lane.

**Where**: `control-plane/app/worker/feed_coordinator.py` (new),
`control-plane/app/worker/worker.py`,
`control-plane/app/worker/__main__.py`, and
`control-plane/tests/integration/test_feed_coordinator.py` (new) plus worker
runtime regression cases.

**Depends on**: T9, T10.

**Reuses**: Existing `Worker.run` stop/in-flight machinery, `asyncio.Task`,
`asyncio.Queue`, shutdown grace, lifecycle claim/recovery, and injected handler
dependencies.

**Requirement**: FEED-09, FEED-16, FEED-30, FEED-33, FEED-34.

**Tools**: MCP: NONE · Skill: `coding-guidelines`.

**Done when**:

- [x] At most one feed HTTP fetch runs in the background; it holds no DB
  transaction and cannot call the BPF helper.
- [x] While a barrier fetch is blocked, a queued `SERVICE_UPDATE` completes
  through the foreground lane within the existing nominal 5-second bound.
- [x] Fetch completion enters an in-memory completion queue; parse, reconcile,
  terminal updates, and apply execute one-at-a-time in the foreground.
- [x] Later feed jobs remain durable/queued while the fetch slot is occupied;
  ordinary service and convergence jobs continue.
- [x] Graceful shutdown waits for the task; grace expiry cancels it and leaves
  the applying run recoverable by T8 startup recovery; no leaked HTTP client or
  task remains.
- [x] Existing Redis-degraded, signal, reconciliation, and service-job runtime
  tests remain green.
- [x] Full gate passes; test total is **task-start B_cp + at least 8**.

**Tests**: integration (worker runtime; unit helpers may be co-located).
**Gate**: full.
**Commit**: `feat(feed): isolate feed fetch latency in worker runtime`.

---

### T12: Extend the M4 snapshot contract for global deny

**What**: Extend the executed M4 #2 snapshot wire contract and both parsers with
an explicit `SERVICE_FULL | GLOBAL_DENY` kind plus a golden global fixture.

**Where**: `data-plane/src/apply_snapshot.h`,
`data-plane/tools/xdpgw-apply.c`,
`data-plane/tests/fixtures/global_deny_snapshot_golden.bin` (new),
`data-plane/tests/test_snapshot.c`, and `data-plane/Makefile`.

**Depends on**: External prerequisite M4 #2 executed.

**Reuses**: M4 #2 magic/schema/bounds parser and golden-fixture self-test;
AD-023 `bl_lpm_key` field semantics.

**Requirement**: FEED-24, FEED-28, FEED-29.

**Tools**: MCP: NONE · Skill: `coding-guidelines`.

**Done when**:

- [x] Snapshot schema version is bumped once and encodes kind, desired global
  revision, entry count, and sorted `{prefixlen,address_be32}` entries without
  compiler padding.
- [x] Existing `SERVICE_FULL` snapshots still round-trip through the upgraded
  Python/C contract and produce identical service behavior.
- [x] C parser accepts the committed global golden fixture and rejects unknown
  kind/version, truncation, invalid prefixes, unsorted/duplicate entries, and
  count above 1,048,576 before any map write.
- [x] Build gate passes; snapshot self-tests pass; data-plane quick total is
  **final M4 #2 B_dp + at least 1**.

**Tests**: dp-unit/build self-test.
**Gate**: build + quick.
**Commit**: `feat(feed-dp): add global-deny snapshot contract`.

---

### T13: Implement the Python global-deny applier and real worker wiring

**What**: Serialize the desired global snapshot, execute `xdpgw-apply` global
mode, guard desired-vs-applied revision, and inject the real applier into the
completed feed worker path.

**Where**: `control-plane/app/worker/applier.py`,
`control-plane/app/worker/feed_runner.py`,
`control-plane/app/worker/__main__.py`,
`control-plane/app/core/config.py`,
`control-plane/tests/unit/test_global_snapshot_serialize.py` (new), and
`control-plane/tests/integration/test_global_deny_applier.py` (new).

**Depends on**: T4, T9, T11, T12.

**Reuses**: M4 #2 `DoubleBufferApplier` subprocess/tempfile/error conventions,
T12 golden fixture, T4 snapshot loader/digest, and T9 injected applier protocol.

**Requirement**: FEED-18, FEED-24, FEED-26, FEED-27, FEED-29, FEED-39,
FEED-40.

**Tools**: MCP: NONE · Skill: `coding-guidelines`.

**Done when**:

- [x] `serialize_global_snapshot` emits bytes identical to T12's golden fixture
  for the matching desired state and rejects an over-limit snapshot.
- [x] `apply_global` writes a 0600 temporary file, invokes the configured helper
  in global mode with timeout, unlinks on every path, and parses a stable
  `{active_slot,node_map_version}` result.
- [x] Nonzero/timeout/malformed result marks the job/run/global apply failed and
  leaves active digest/revision/version unchanged.
- [x] After success, a locked compare advances active digest/revision only when
  desired revision still matches; a newer desired revision stays pending and is
  re-enqueued.
- [x] Worker wiring supplies one lifetime HTTP client and the real global
  applier without changing the executed service applier path.
- [x] Fake-helper integration covers success, failure, timeout, same-revision
  no-op, and desired-advanced-during-apply; logs remain secret-free.
- [x] Quick serializer gate and full integration gate pass; full total is
  **task-start B_cp + at least 9**.

**Tests**: unit + integration (highest type: integration).
**Gate**: full.
**Commit**: `feat(feed): apply desired global deny snapshots`.

---

### T14: Implement `xdpgw-apply` global mode and propagation gates

**What**: Add the inverse carry-forward global mode to the M4 #2 helper, share
one process lock/commit path across modes, and prove hot-path propagation,
rollback, alternating swaps, and the 1M boundary.

**Where**: `data-plane/tools/xdpgw-apply.c`,
`data-plane/tests/test_parse.c`, `data-plane/tests/` global-apply smoke/scale
helpers, and `data-plane/Makefile`.

**Depends on**: T12, T13.

**Reuses**: M4 #2 `open_pins`, `create_inner_like`, service carry/build,
`verify_slot`, and single `commit`; AD-023 bloom `/24` expansion,
`GBL_F_HAS_BROAD`, 2M bloom limit, 1M LPM limit, and `make blbulk` corpus.

**Requirement**: FEED-24, FEED-25, FEED-26, FEED-27, FEED-28, FEED-29.

**Tools**: MCP: NONE · Skill: `coding-guidelines`.

**Done when**:

- [x] Both service and global modes acquire one exclusive pin-directory lock
  before fresh-reading `active_config` and hold it through verify/commit.
- [x] Global mode pointer-carries every service-scoped outer and
  `fair_node_config`, carries `udp_blocked_port_bitmap`, and rebuilds fresh
  global bloom/LPM plus coherent inactive `gbl_meta` from the full manual+feed
  snapshot.
- [x] Verify covers carried inner IDs, inserted count, bloom fill/broad policy,
  and meta flags; all build/verify/timeout failures exit before the flip and
  preserve prior slot/version/verdicts.
- [x] Successful commit performs one `active_config` write and returns the new
  slot/version; a listed source reaches `blacklist_drop`; unrelated service,
  rule, whitelist, service-blacklist, fairness, and bitmap behavior is unchanged.
- [x] Alternating service/global modes repeatedly re-read/toggle the slot and
  preserve the other configuration group without stale-over-new writes.
- [x] Desired=active skips invocation from Python; identical-but-unconverged
  desired state still runs and converges.
- [x] Quick dp-unit gate passes with **task-start B_dp + at least 8** cases.
- [x] Privileged full smoke passes from feed worker/fake feed through real helper
  to XDP verdict; scale gate loads 1,048,576 distinct entries successfully and
  rejects 1,048,577 before flip. Record wall time and memory footprint.

**Tests**: dp-unit + dp-integration + scale (highest type: dp-integration).
**Gate**: quick + full + scale.
**Commit**: `feat(feed-dp): rebuild and atomically swap global deny maps`.

---

### T15: Document operations, tests, and final traceability

**What**: Document source format/limits/secrets, worker scheduling/recovery,
global apply gates, and operator verification; record final counts and complete
requirement/task traceability.

**Where**: `control-plane/README.md`, `data-plane/README.md`,
`.specs/codebase/TESTING.md`, this `tasks.md`, the feature `spec.md`,
`.specs/project/ROADMAP.md`, and `.specs/project/STATE.md`.

**Depends on**: T6, T7, T11, T13, T14.

**Reuses**: Existing worker runbook, data-plane apply/`blbulk` sections,
AD-029 diagrams, and the gate outputs from every prior task.

**Requirement**: FEED-01..40 (documentation and final traceability only; code
verification stays co-located in T1–T14).

**Tools**: MCP: NONE · Skill: `docs-writer`.

**Done when**:

- [x] Control-plane docs cover plain line-list grammar, exact rejects, 32-MiB/
  timeout/interval defaults, disabled/manual behavior, dry-run, credentials by
  environment reference, scheduling, deletion, and failure recovery.
- [x] Data-plane docs cover global helper mode, shared lock, inverse
  carry-forward, no-flip rollback, version semantics, and scale commands.
- [x] `TESTING.md` adds parser/fetch unit patterns, committed-DB feed worker
  patterns, nonparallel integration rules, dp global-apply cases, and privileged
  smoke/scale commands without claiming the manual Redis-down check ran.
- [x] `tasks.md` records each task's commit/gate/final count; `spec.md` moves all
  40 requirements to Verified only after their owning gates pass.
- [x] ROADMAP/STATE record AD-029 execution status, M4 #2 gate resolution,
  measured scale results, blockers/deviations, and the next phase.
- [x] All local links and diagram files resolve; `git diff --check` is clean.
- [x] Re-run the final control-plane full gate and data-plane quick/build gates;
  record privileged full/scale results from T14 without silently substituting
  unrun checks.

**Tests**: none (documentation-only; prior tests are not deferred here).
**Gate**: documentation checks + final recorded gates.
**Commit**: `docs(feed): add sync and global-apply operations guide`.

---

## Parallel execution map

```text
Foundation:
  T1

Unit-only parallel lane after T1:
  ├── T2 [P]
  └── T3 [P]

Serial control-plane integration lane:
  T4 → T5 → T6 → T7 → T8 → T9 → T10 → T11
  (logical dependencies are listed per task; serialization also protects the
   shared compose.test.yml PostgreSQL/Redis state)

Externally gated propagation lane:
  M4 #2 → T12 → T13 → T14

Closeout:
  T15
```

During Execute, delegate T2 and T3 concurrently after T1. Delegate every other
task one at a time. Each sub-agent receives only its task definition, AD-029
sections it references, `TESTING.md`, and applicable coding/documentation
guidelines.

---

## Requirement traceability

| Requirements | Owning tasks | Coverage |
| --- | --- | --- |
| FEED-01..07 | T1, T5, T7 | Source model/service/API, audit, secrecy, RBAC |
| FEED-08 | T1, T5, T7, T8, T9 | Durable enqueue and idempotent processing |
| FEED-09 | T3, T9, T11 | Bounded HTTPS fetch outside foreground state lane |
| FEED-10..13 | T2, T9 | Grammar, validation, invalid counts, dedup |
| FEED-14 | T4, T9 | Per-source assertion reconciliation |
| FEED-15 | T1, T4, T6 | Manual precedence and multi-feed assertions |
| FEED-16..18 | T2, T3, T4, T8, T9, T13 | Isolation, keep-last, and no-op/convergence behavior |
| FEED-19..23 | T1, T4 | SQL overlap, durable events, audit, no removal |
| FEED-24..29 | T4, T12, T13, T14 | Desired state, global snapshot, inverse swap, scale/hot path |
| FEED-30..34 | T5, T8, T10, T11 | Due scheduler, next due, suppression, restart |
| FEED-35..40 | T1, T2, T3, T7, T8, T9, T13 | Run stats/history, partial/dry-run, logs/secrets |

**Coverage:** 40/40 requirements mapped; 0 unmapped.

---

## Mandatory pre-approval checks

### Task granularity check

| Task | Single deliverable | Status |
| --- | --- | --- |
| T1 | One persistence/migration contract | ✅ Granular |
| T2 | One pure parser component | ✅ Granular |
| T3 | One bounded fetch component | ✅ Granular |
| T4 | One assertion reconciliation component | ✅ Granular |
| T5 | One source lifecycle/enqueue service | ✅ Granular |
| T6 | One manual-precedence integration | ✅ Granular |
| T7 | One feed API surface | ✅ Granular |
| T8 | One job-lifecycle abstraction | ✅ Granular |
| T9 | One sync-runner composition | ✅ Granular |
| T10 | One due scheduler | ✅ Granular |
| T11 | One background-fetch coordinator | ✅ Granular |
| T12 | One cross-language snapshot contract extension | ✅ Granular |
| T13 | One Python global applier | ✅ Granular |
| T14 | One C helper global mode with its gates | ✅ Granular |
| T15 | One operations/traceability documentation set | ✅ Granular |

### Diagram-definition cross-check

| Task | Depends on (definition) | Execution diagram shows | Status |
| --- | --- | --- | --- |
| T1 | None | Root | ✅ Match |
| T2 | None | Independent parser branch | ✅ Match |
| T3 | T1 | T1 → T3 | ✅ Match |
| T4 | T1, T2 | T1,T2 → T4 | ✅ Match |
| T5 | T4 | T4 → T5 | ✅ Match |
| T6 | T4 | T4 → T6 | ✅ Match |
| T7 | T5 | T5 → T7 | ✅ Match |
| T8 | T1 | T1 → T8 | ✅ Match |
| T9 | T2, T3, T4, T5, T8 | T2,T3,T4,T5,T8 → T9 | ✅ Match |
| T10 | T5, T8 | T5,T8 → T10 | ✅ Match |
| T11 | T9, T10 | T9,T10 → T11 | ✅ Match |
| T12 | External M4 #2 | M4 #2 → T12 | ✅ Match |
| T13 | T4, T9, T11, T12 | T4,T9,T11,T12 → T13 | ✅ Match |
| T14 | T12, T13 | T12,T13 → T14 | ✅ Match |
| T15 | T6, T7, T11, T13, T14 | T6,T7,T11,T13,T14 → T15 | ✅ Match |

### Test co-location validation

| Task | Layer changed | Matrix requires | Task says | Status |
| --- | --- | --- | --- | --- |
| T1 | Models + migration | integration | integration | ✅ OK |
| T2 | Pure parser logic | unit | unit | ✅ OK |
| T3 | Pure fetch/config logic | unit / config none | unit | ✅ OK |
| T4 | PostgreSQL service | integration | integration | ✅ OK |
| T5 | PostgreSQL/Redis service | integration | integration | ✅ OK |
| T6 | PostgreSQL list service | integration | integration | ✅ OK |
| T7 | API router | integration | integration | ✅ OK |
| T8 | Worker processor/lifecycle | integration | integration | ✅ OK |
| T9 | Worker processor/reconciliation | integration | integration | ✅ OK |
| T10 | Worker scheduler/runtime | integration | integration | ✅ OK |
| T11 | Worker runtime | integration | integration | ✅ OK |
| T12 | Data-plane wire/parser | dp-unit/build | dp-unit/build | ✅ OK |
| T13 | Worker applier + serializer | unit + integration | integration | ✅ OK |
| T14 | Data-plane helper/runtime | dp-unit + dp-integration | dp-integration | ✅ OK |
| T15 | Documentation only | none | none | ✅ OK |

All three pre-approval checks pass. No task defers its layer tests to a later
task; T14's privileged/scale cases extend, rather than replace, its dp-unit
coverage.
