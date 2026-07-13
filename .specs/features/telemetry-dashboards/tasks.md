# Telemetry & Dashboards Tasks

**Design**: `.specs/features/telemetry-dashboards/design.md` (AD-030)
**Spec**: `.specs/features/telemetry-dashboards/spec.md` (TEL-01..40)
**Status**: P1 verified (2026-07-13)

**Confirmed defaults carried from Design review** (the 3 flags):
- D-030-6 — aggregation is a **worker background task**, no `TELEMETRY_AGGREGATE` `JobType`. ✅
- D-030-4 — `dp_id` assigned in `create_service` (touches executed M1 code, additive). ✅
- D-030-3 — `svc_stat` value uses `drop_by_reason[DROP_REASON_CAP=32]`. ✅

**Execution scope (2026-07-13):**
- The completed P1 scope includes T1–T12 and T17. This follow-on execution
  includes the P2 tasks T13–T15 and the P3 task T16.
- `worker_telemetry_interval_seconds` is an integer in the inclusive range
  1–2 seconds. The default is 2 seconds, so persisted `window_seconds` is
  exact.
- Production SPA serving is opt-in through
  `CONTROL_PLANE_FRONTEND_STATIC_DIR`. FastAPI serves the built bundle only
  when that directory is configured. Its history fallback returns HTML only
  for browser routes and preserves 404 responses for API and missing asset
  paths.

**Baselines:** Capture fresh control-plane and frontend gate counts before T7.
Do not use the historic counts below as a current baseline.

**Execution status (2026-07-13):**

| Task | Status | Evidence or next action |
| --- | --- | --- |
| T1 | Complete | `e77b114`; 112 DP tests reported green |
| T2 | Complete | `9b7cecb`; 112 DP tests + redirect/fairness smoke + pin lifecycle green |
| T3 | Complete | `5684f63`; build, offline, live native snapshot, JSON, and pin lifecycle green |
| T4 | Complete | `bfe4376`; full CP gate: 392 passed |
| T5 | Complete | `7ca62db` + `657cd3d`; full CP gate: 395 passed |
| T6 | Complete | `7f879c8`; quick CP gate: 97 passed, 295 deselected |
| T7 | Complete | `8efba3b`; full CP gate: 441 passed |
| T9 | Complete | `c2d4239`; full CP gate: 444 passed |
| T10 | Complete | `f58903a`; frontend lint/typecheck/build + 5 Vitest tests green |
| T8 | Complete | `17f0aa9`; full CP gate: 448 passed |
| T11 | Complete | `77753e4`; FE gate: 5 files / 8 tests passed |
| T12 | Complete | `6e335e8`; FE gate: 9 files / 13 tests; CP static-serving checks: 2 passed |
| T13 | Complete | DP build and 130 DP tests; CP full gate passed with 452 tests; focused follow-up passed with 19 tests |
| T14–T15 | Pending | Execute after T13 because the CP integration fixtures serialize these tasks |
| T16 | Pending | Execute after the P2 work to validate the final dashboard surface together |
| T17 | Complete | docs render review and final P1 traceability recorded |

T3 additionally pins `active_config` with the established observability
lifecycle: the approved `active{slot,version}` snapshot contract is otherwise
unreadable by `dpstat`. P1 completed as T7 → T9 → (T8 ∥ T11) → T12 → T17.
Final validation: CP full gate **450 passed** (18 existing Pydantic
deprecation warnings); FE gate **9 files / 13 tests** plus production build;
DP build/quick **130 passed** and privileged redirect/fairness/apply smoke;
and browser validation of FastAPI-served `/tenant` and `/admin` deep links,
login redirect, tenant isolation, two-second polling, critical generic XDP,
and missing-asset 404 behavior. TEL-01–30, TEL-36, and TEL-37 are verified.
The remaining P2/P3 requirements are pending their scheduled tasks.

**Tracks:** **DP** (`data-plane/`, C) · **CP** (`control-plane/app`, Python) · **FE** (`control-plane/frontend/`, React/TS). DP and CP/FE are separate dirs with separate gates → **cross-track parallel**. Within CP, only **unit**-typed tasks may be `[P]` (integration shares `compose.test.yml`). FE is a separate toolchain (Vitest, no compose.test) → the FE scaffold is `[P]`, but FE view tasks serialize on the shared project tree.

**Gate legend** (from TESTING.md): DP `build`=`make bpf skel loader dpstat`, `quick`=`make test`, `full`=`make test && sudo make smoke`. CP `quick`=`ruff+ruff format+mypy+pytest -m unit`, `full`=`+pytest -q` on `compose.test.yml`, `build`=import smoke + `alembic upgrade head`. **FE `fe`** (new, documented by T17) = `cd control-plane/frontend && npm run lint && npm run typecheck && npm run test -- --run && npm run build`.

---

## Execution Plan

```
Phase 1 — Foundations (parallel across tracks)
  T1  (DP core: svc_stat + frame_len + wiring)     data-plane/
  T4  (CP: dp_id migration+model+create_service)   control-plane/  [CP-integration]
  T6  (CP: TelemetryReader) [P]                     control-plane/  [unit]
  T10 (FE: SPA scaffold + auth shell) [P]           control-plane/frontend/

Phase 2
  T1 → T2  (DP: loader pin + env-seed dp_id)
  T4 → T5  (CP: telemetry models + migration)

Phase 3
  T1,T2 → T3   (DP: dpstat snapshot --json)
  T5,T6 → T7   (CP: TelemetryAggregator)     ┐ CP-integration:
  T5    → T9   (CP: telemetry API)           ┘ T7 then T9 (serialize on infra); T3 runs parallel on DP

Phase 4
  T7 → T8            (CP: worker wiring + settings)
  T9,T10 → T11       (FE: tenant dashboard)          T8 ∥ T11 (different tracks)

Phase 5
  T9,T10,T11 → T12   (FE: admin dashboard)           (after T11 — shared FE tree)

Phase 6 — P2 follow-on execution
  T3,T7  → T13  (top-talkers backend)
  T7,T9  → T14  (richer-health backend)     [T13,T14 CP-integration → serialize]
  T12,T13,T14 → T15 (P2 frontend panels)

Phase 7 — P3 follow-on execution
  T9,T12 → T16 (trend chart + export)

Phase 8 — Docs
  T17 (TESTING.md dp+fe conventions, README/dashboards)
```

---

## Task Breakdown

### T1: Per-service counters + hot-path wiring (DP core)

**What**: New `svc_stat.h` (per-CPU per-service counter map + `svc_stat_clean`/`svc_stat_drop` helpers), `pkt_meta.frame_len`, and wiring at the two choke points.
**Where**: `data-plane/src/svc_stat.h` (new), `src/pkt_meta.h`, `src/xdp_gateway.bpf.c`, `src/drop_reason.h`, `tests/test_parse.c`
**Depends on**: None
**Reuses**: `rate_limit_state` prealloc-`PERCPU_HASH` idiom (`rules.h`); `__sync_fetch_and_add` from `record_drop`; `DROP_REASON_CAP`
**Requirement**: TEL-01..06

**Tools**: Skill `coding-guidelines`; MCP: none

**Done when**:
- [ ] `svc_stat_map` = `PERCPU_HASH`, `max_entries=1024`, key `__u32 dp_id`, value `struct svc_stat {u64 clean_pkts,clean_bytes,drop_pkts,drop_bytes; u64 drop_by_reason[DROP_REASON_CAP];}`, prealloc; include-cycle avoided (svc_stat.h included in `drop_reason.h` after the enum, guarded)
- [ ] `pkt_meta.frame_len` (`__u16`, repurposed from `_pad2`), struct **stays 40 B** (`_Static_assert` holds); set `meta.frame_len = data_end - data` at ingress top of `xdp_gateway`
- [ ] `svc_stat_clean(meta)` called in `redirect_out` (before `bpf_redirect_map`); `svc_stat_drop(meta,key)` called in `record_drop` (after `counter_map` bump); both no-op when `meta->service_id == 0`
- [ ] `meta->service_id = service->service_id` moved **above** the `enabled` check so `service_disabled` attributes to the service
- [ ] dp-unit: clean packet → service's `clean_pkts/clean_bytes(=frame_len)` exact; drop → `drop_pkts/drop_bytes` + `drop_by_reason[reason]`; `service_miss`/IPv6/ARP touch **only** node-global (dp_id 0 uncounted); reload→zero documented
- [ ] Gate passes: `make test` — count = `B_dp + N` (record N; no deletions)

**Tests**: dp-unit
**Gate**: quick
**Commit**: `feat(telemetry): exact per-service hot-path counters`

---

### T2: Loader pin + env-seed dp_id (DP)

**What**: Pin `svc_stat_map` under `/sys/fs/bpf/xdp_gateway/`; loader env-seed writes `service_val.service_id = dp_id`.
**Where**: `data-plane/loader/loader.c`
**Depends on**: T1
**Reuses**: `set_observability_pin_paths`/`pin_observability_maps`/`unpin_observability_maps` group; existing `SERVICE_DEST` seed
**Requirement**: TEL-06 (bounded/attributable), enables T3 read

**Tools**: Skill `coding-guidelines`

**Done when**:
- [ ] `SVC_STAT_PIN_PATH` added to the three observability pin/unpin helpers (pin on load, unpin+rmdir on detach, rollback on error)
- [ ] Env-seed accepts a `dp_id` (e.g. `SERVICE_DP_ID`, default 1) written into the seeded `service_val.service_id` so a loaded gateway attributes per-service counters pre-M4#2
- [ ] `make bpf skel loader dpstat` builds; `sudo make smoke` still green (pin present under `/sys/fs/bpf/xdp_gateway/svc_stat_map`, Ctrl-C removes it)
- [ ] Gate passes: `make test && sudo make smoke` — `make test` count unchanged from T1

**Tests**: dp-integration
**Gate**: full
**Commit**: `feat(telemetry): pin svc_stat_map and seed dp_id`

---

### T3: `dpstat snapshot --json` reader (DP)

**What**: New one-shot `snapshot --json` subcommand emitting the telemetry snapshot contract.
**Where**: `data-plane/tools/dpstat.c`, `data-plane/tests/fixtures/telemetry_snapshot_golden.json` (new, shared contract with T6)
**Depends on**: T1, T2
**Reuses**: `open_pinned_map`, `read_percpu_u64`, `libbpf_num_possible_cpus`, `drop_reason_name[]`
**Requirement**: TEL-07/08/12

**Tools**: Skill `coding-guidelines`; MCP `context7` (verify `bpf_xdp_query`), else web search (design-flagged fact)

**Done when**:
- [ ] `dpstat snapshot --json [--ifindex N]` prints one JSON object: `active{slot,version}`, `xdp{mode,prog_id,ifindex}` (via `bpf_xdp_query`; `unknown` if no ifindex), `node{counters,sample_stats,bloom_stats}` (per-CPU summed), `services[]{dp_id,clean_pkts,clean_bytes,drop_pkts,drop_bytes,drop_by_reason{}}` (iterate `svc_stat_map` via `bpf_map_get_next_key`)
- [ ] No pins → exit nonzero with the friendly "gateway not loaded" message (mode `offline`), not a crash
- [ ] Committed golden fixture `telemetry_snapshot_golden.json` matches the emitted schema (byte-shape asserted by a build/manual self-check); reused by T6
- [ ] `bpf_xdp_query` signature/behavior confirmed via Context7/web (record source)
- [ ] Gate passes: `make bpf skel loader dpstat` builds; manual: load gateway → `dpstat snapshot --json` emits valid JSON parsed by `python -m json.tool`

**Tests**: build + manual
**Gate**: build
**Commit**: `feat(telemetry): dpstat snapshot --json reader`

---

### T4: `ProtectedService.dp_id` surrogate (CP)

**What**: `dp_id` column + `service_dp_id_seq` + assignment in `create_service` + backfill migration.
**Where**: `control-plane/app/db/models.py`, `app/services/services.py`, `migrations/versions/20260710_0007_service_dp_id.py` (new), `tests/integration/`
**Depends on**: None
**Reuses**: `create_service`, `TimestampMixin`, migration pattern (`20260710_0006`)
**Requirement**: TEL-01/06 (u32↔UUID join, D-030-4)

**Tools**: Skill `coding-guidelines`

**Done when**:
- [ ] `ProtectedService.dp_id: Mapped[int]` (`Integer`, `unique`, `nullable=False`); migration creates `service_dp_id_seq`, adds the column, **backfills** existing rows via `nextval`, sets NOT NULL + unique
- [ ] `create_service` assigns `dp_id = nextval('service_dp_id_seq')` (monotonic, ≥1, never reused); `0` never issued
- [ ] Migration `down_revision = "20260710_0006"`; `alembic upgrade head` then `downgrade` clean on test DB
- [ ] Integration: new service gets a unique dp_id; two services get distinct ids; delete+create does not reuse; backfill assigns all existing
- [ ] Gate passes: `full` — count = `B_cp + N`

**Tests**: integration
**Gate**: full
**Commit**: `feat(telemetry): add ProtectedService.dp_id surrogate`

---

### T5: Telemetry data models + migration (CP)

**What**: `TelemetryCounter` + `NodeHealthSnapshot` models, enums, migration.
**Where**: `control-plane/app/db/models.py`, `migrations/versions/20260710_0008_telemetry.py` (new), `tests/integration/`
**Depends on**: T4
**Reuses**: `Base`, `JSONB`, `native_enum=False` enum pattern, FK `ondelete` idioms
**Requirement**: TEL-10/12/14

**Tools**: Skill `coding-guidelines`

**Done when**:
- [ ] `TelemetryCounter` (scope `service|node`, `service_id`→`protected_service` `ondelete=SET NULL`, `dp_id`, `window_start`, `window_seconds`, clean/drop pkts+bytes `BigInteger`, `drop_by_reason` JSONB, `pps`/`bps`, `top_dst_ports`/`top_src` JSONB nullable, `is_baseline`, `created_at`; indexes on `(scope,service_id,window_start desc)` and `(scope,window_start desc)`)
- [ ] `NodeHealthSnapshot` (`captured_at`, `window_seconds`, `xdp_mode` enum, `active_slot`, `map_version`, `map_error_count`, `node_clean_bps`, `node_capacity_bps`, `bloom_stats` JSONB; index `(captured_at desc)`)
- [ ] Migration `down_revision="20260710_0007"`; `upgrade`/`downgrade` clean; `TelemetryScope`/`XdpMode` enums
- [ ] Integration: insert service+node rows, `SET NULL` on service delete keeps historical rows, JSONB round-trips
- [ ] Gate passes: `full` — count = prior + N

**Tests**: integration
**Gate**: full
**Commit**: `feat(telemetry): TelemetryCounter and NodeHealthSnapshot models`

---

### T6: `TelemetryReader` + snapshot parsing (CP) [P]

**What**: Subprocess wrapper + `FakeTelemetryReader` + typed snapshot dataclasses + JSON parsing.
**Where**: `control-plane/app/worker/telemetry_reader.py` (new), `tests/unit/test_telemetry_reader.py`
**Depends on**: None (targets the documented JSON contract + `telemetry_snapshot_golden.json` fixture shared with T3)
**Reuses**: `asyncio.create_subprocess_exec`+timeout pattern (M4#2 applier), `FeedCoordinator` DI seam
**Requirement**: TEL-08 (read), TEL-13 (reader failure → None)

**Tools**: Skill `coding-guidelines`

**Done when**:
- [ ] `TelemetrySnapshot`/`ServiceCounters`/`NodeCounters` dataclasses; `snapshot()` execs the binary and parses stdout; nonzero/"not loaded"/timeout → `None`
- [ ] `FakeTelemetryReader(snapshots=[...])` returns canned snapshots (no kernel) for downstream tests
- [ ] Unit: parse the committed golden fixture into typed objects (round-trip vs T3's schema); malformed JSON → `None`; offline sentinel → `None`
- [ ] Gate passes: `quick` — count = `B_cp + N`

**Tests**: unit
**Gate**: quick
**Commit**: `feat(telemetry): telemetry reader and snapshot parsing`

---

### T7: `TelemetryAggregator` — delta/reset/persist/prune (CP)

**What**: `aggregate_once` (delta + reset detection + `dp_id→UUID` map + persist service/node rows + `NodeHealthSnapshot` + prune) and `run_loop`.
**Where**: `control-plane/app/worker/telemetry.py` (new), `tests/integration/`, `tests/unit/` (delta math)
**Depends on**: T5, T6
**Reuses**: `session_scope`, `reconcile_once` "own session per pass" shape, `ProtectedService.dp_id`, `node_clean_capacity_gbps`
**Requirement**: TEL-07/09/10/11(node)/12/13/14

**Tools**: Skill `coding-guidelines`

**Done when**:
- [x] `aggregate_once`: reader `None` → write `xdp_mode=offline` health + return; else per-key delta vs in-memory previous; **reset detection** (delta<0 or `active.version` changed → previous=0); first tick seeds baseline (`is_baseline`, no deltas)
- [x] `dp_id→(service UUID, tenant)` cache from PG (refresh per tick); unknown dp_id → excluded from per-service rows, still in node totals (TEL-06)
- [x] Persist `TelemetryCounter` (service + node scope) with pps/bps=delta÷window; `NodeHealthSnapshot` (mode/slot/version/map_error/clean_bps/capacity); prune windows older than retention
- [x] `run_loop(stop)`: loop `aggregate_once` + interruptible sleep(interval); any error → log + continue (never raises out)
- [x] Unit (FakeReader): delta correctness, reset→raw value, baseline-first-tick, unknown-dp_id handling. Integration: rows persisted, prune removes old, DB-down tick skipped without crash
- [x] Gate passes: `full` — 441 passed

**Tests**: integration
**Gate**: full
**Commit**: `feat(telemetry): windowed aggregator with reset detection`

---

### T8: Worker integration + settings (CP)

**What**: Spawn the aggregator lane in `Worker.run`; wire it in `__main__`; add `worker_telemetry_*` settings.
**Where**: `control-plane/app/worker/worker.py`, `app/worker/__main__.py`, `app/core/config.py`, `tests/integration/test_worker_runtime.py`, `tests/unit/`
**Depends on**: T7
**Reuses**: feed background-lane spawn/await/cancel lifecycle; `worker_*` settings convention
**Requirement**: TEL-07 (cadence), TEL-14 (isolation)

**Tools**: Skill `coding-guidelines`

**Done when**:
- [x] `Worker.__init__` takes optional `telemetry`; `run()` spawns `telemetry.run_loop(stop)` before the main loop and awaits/cancels it in `finally` (mirrors feed lane); job processing unaffected when the lane errors
- [x] `__main__._run_worker` builds `TelemetryReader`+`TelemetryAggregator` from settings and injects them (skipped when `worker_telemetry_enabled=False`)
- [x] `Settings`: `worker_telemetry_enabled=True`, integer `worker_telemetry_interval_seconds=2` constrained to 1–2 inclusive, `worker_telemetry_retention_seconds`, `worker_telemetry_binary_path`, `worker_telemetry_ifindex: int|None`, `worker_telemetry_timeout_seconds=5.0`
- [x] Integration: runtime with a `FakeTelemetryReader` produces rows on the cadence and cancels cleanly on stop; a raising aggregator does not stop job processing. Unit: settings defaults
- [x] Gate passes: `full` — 448 passed

**Tests**: integration
**Gate**: full
**Commit**: `feat(telemetry): run aggregator lane in the worker`

---

### T9: Telemetry & health API (CP)

**What**: `telemetry` router (service + node + health endpoints) + schemas, registered in the app.
**Where**: `control-plane/app/api/routers/telemetry.py` (new), `app/api/schemas/telemetry.py` (new), `app/main.py`, `tests/integration/`
**Depends on**: T5
**Reuses**: `load_service_for_principal` (404 cross-tenant), `require_admin`, `get_current_user`, `get_db`, router/schema conventions; live `AgentJob`/`FeedSyncRun` reads
**Requirement**: TEL-16..21

**Tools**: Skill `coding-guidelines`

**Done when**:
- [x] `GET /services/{service_id}/telemetry` via `load_service_for_principal` → latest service window → `ServiceTelemetryResponse` (counts, `drop_by_reason`, pps/bps, `window_start`, `window_seconds`, `stale`); no row → 200 `{has_data:false}` zeroed
- [x] `GET /node/telemetry` + `GET /node/health` via `require_admin` → latest node window + `NodeHealthSnapshot` + **live** `AgentJob` backlog (count by status) + `FeedSyncRun`/`ThreatFeedSource` last status + throughput-vs-`node_clean_capacity_gbps`
- [x] `stale` computed from `window_start` age vs `2×interval`; router registered in `create_app`
- [x] Integration (AsyncClient): owner tenant 200; **non-owner 404**; admin node view 200; empty-state 200; endpoints read-only
- [x] Gate passes: `full` — 444 passed

**Tests**: integration
**Gate**: full
**Commit**: `feat(telemetry): telemetry and node-health API`

---

### T10: SPA scaffold + auth shell (FE) [P]

**What**: Bootstrap the Vite+React+TS project: API client, auth context, role-aware routing, layout, test setup.
**Where**: `control-plane/frontend/` (new): `package.json`, `vite.config.ts`, `src/{main.tsx,api/client.ts,auth/AuthContext.tsx,routes/ProtectedRoute.tsx,layout/AppLayout.tsx,pages/LoginPage.tsx}`, `src/**/*.test.tsx`
**Depends on**: None (uses existing `/auth/login`, `/auth/me`, `/auth/logout`)
**Reuses**: existing auth endpoints; establishes the shell later CRUD screens inherit
**Requirement**: TEL-22, TEL-30

**Tools**: Skill `coding-guidelines`; MCP `context7` (Vite/React/React Router/TanStack Query/Recharts current stable), else web

**Done when**:
- [x] Vite+React+TS project with React Router, TanStack Query (`QueryClientProvider`), Recharts, Vitest+RTL; deps pinned; `npm run {lint,typecheck,test,build}` scripts exist
- [x] `api/client.ts`: `fetch(..., {credentials:"include"})`, 401 → redirect `/login`; `AuthContext` (login via `POST /auth/login`, role via `GET /auth/me`, logout); `ProtectedRoute` gates by auth + role; `AppLayout`; `LoginPage`
- [x] Vitest unit: api-client 401→redirect, `ProtectedRoute` role gating, auth context login flow (mocked fetch)
- [x] Gate passes: `fe` — 5 Vitest tests; typecheck+lint+build clean
- [x] Dev proxy configured (`/auth`,`/services`,`/node` → FastAPI)

**Tests**: fe-unit
**Gate**: fe
**Commit**: `feat(telemetry): bootstrap React SPA shell`

---

### T11: Tenant dashboard (FE)

**What**: Tenant service list + per-service telemetry panel with charts, polling, staleness.
**Where**: `control-plane/frontend/src/pages/TenantDashboard.tsx`, `src/components/{ServiceList,ServiceTelemetryPanel,CleanVsDropChart,DropReasonChart,RateTiles,StalenessBadge}.tsx`, `src/hooks/useServiceTelemetry.ts`, `*.test.tsx`
**Depends on**: T9, T10
**Reuses**: T10 shell/client/auth; `GET /services/{id}/telemetry`
**Requirement**: TEL-23, TEL-25, TEL-26, TEL-28

**Tools**: Skill `coding-guidelines`

**Done when**:
- [x] `TenantDashboard` lists the tenant's services and renders `ServiceTelemetryPanel` (clean-vs-drop, drop-reason distribution chart, pps/bps tiles)
- [x] `useServiceTelemetry(id)` (TanStack `refetchInterval:2000`) updates in place; `StalenessBadge` from `window_start`/`stale`; loading/empty/error states
- [x] Vitest unit: panel renders from mock payload; staleness badge shows on stale/`has_data:false`; poll interval configured
- [x] Gate passes: `fe` — 5 files / 8 tests passed

**Tests**: fe-unit
**Gate**: fe
**Commit**: `feat(telemetry): tenant service telemetry dashboard`

---

### T12: Admin dashboard and production SPA serving (FE + CP)

**What**: Admin node health + node telemetry views, XDP-mode critical flag, and
opt-in FastAPI serving of the production SPA bundle.
**Where**: `control-plane/frontend/src/pages/AdminDashboard.tsx`,
`src/components/{NodeHealthPanel,NodeTelemetryPanel,XdpModeFlag,ThroughputGauge}.tsx`,
`src/hooks/useNodeTelemetry.ts`, `*.test.tsx`, `control-plane/app/main.py`,
`control-plane/app/core/config.py`,
`control-plane/tests/unit/test_frontend_static_serving.py`
**Depends on**: T9, T10, T11 (shared FE tree — runs after T11)
**Reuses**: T10 shell; `GET /node/telemetry`, `/node/health`; FastAPI
`StaticFiles` mount pattern
**Requirement**: TEL-24, TEL-27, TEL-29, TEL-28

**Tools**: Skill `coding-guidelines`

**Done when**:
- [x] `AdminDashboard` renders `NodeHealthPanel` (xdp mode, map version, map_error, backlog, feed status, throughput gauge) + `NodeTelemetryPanel` (node counters/distribution), polling ≤2s
- [x] XDP mode `generic`/`offline` → visually flagged critical; staleness + loading/empty/error states
- [x] Vitest unit: health panel renders from mock; XDP-mode flag critical on generic/offline; throughput gauge computes vs capacity
- [x] `CONTROL_PLANE_FRONTEND_STATIC_DIR` opt-in config enables FastAPI to serve the built Vite bundle. Browser history routes return `index.html`; existing API routes and missing static assets continue to return 404 rather than the SPA HTML.
- [x] Focused CP test covers opt-in serving, a browser history fallback, and preserved API/asset 404 behavior.
- [x] Gate passes: `fe` — 9 files / 13 tests; CP `ruff`, format, `mypy`, import checks, and 2 static-serving tests are clean.

**Tests**: fe-unit + cp-unit
**Gate**: fe + CP lint/type/import + focused static-serving test
**Commit**: `feat(telemetry): admin node health dashboard`

---

### T13: Top-talkers backend (DP+CP, P2)

**What**: `dpstat tail --json` streaming lane + aggregator rolling top-N (dst-port, src IP) persisted to `top_*`.
**Where**: `data-plane/tools/dpstat.c`, `control-plane/app/worker/telemetry_reader.py`, `app/worker/telemetry.py`, tests
**Depends on**: T3, T7
**Reuses**: `dpstat tail` ringbuf reader; `drop_event` fields; aggregator persistence
**Requirement**: TEL-36, TEL-37

**Tools**: Skill `coding-guidelines`; MCP `context7`/web (ringbuf consumer-pos across processes — design-flagged; fallback = long-lived streaming lane)

**Done when**:
- [x] `dpstat tail --json` streams sampled `drop_event`s as JSON lines; aggregator maintains a rolling per-service+node top-N (dst-port, src IP) over a configurable window and persists to `top_dst_ports`/`top_src`.
- [x] Ringbuf consumption uses one long-lived consumer lane. The top-talker
  data is sampled/approximate, T15 labels it in the UI, and `top_src` carries
  the CM-08 pilot-PII note.
- [x] Integration uses `FakeTelemetryReader` sample events; DP build and its unchanged test suite pass.
- [x] DP `build` and the CP `full` gate pass.

**Tests**: integration
**Gate**: full
**Commit**: `feat(telemetry): sampled top-talkers aggregation`

---

### T14: Richer node-health backend (CP, P2)

**What**: Surface bloom hit/FP, fairness committed-honored, backlog+apply detail, per-source feed status.
**Where**: `control-plane/app/worker/telemetry.py`, `app/api/routers/telemetry.py`, `app/api/schemas/telemetry.py`, tests
**Depends on**: T7, T9
**Reuses**: `bloom_stats` from snapshot, `ServicePlan.committed_clean_gbps`, `AgentJob`, `FeedSyncRun`
**Requirement**: TEL-31, TEL-32, TEL-33, TEL-34

**Tools**: Skill `coding-guidelines`

**Done when**:
- [ ] Aggregator/health capture bloom hit/FP (`bloom_stats`) and committed-honored (service clean bps vs `committed_clean_gbps`); API exposes backlog+last-apply detail and per-source feed status
- [ ] Integration: each metric computed/served correctly
- [ ] Gate passes: `full`

**Tests**: integration
**Gate**: full
**Commit**: `feat(telemetry): richer node-health metrics`

---

### T15: P2 frontend panels (FE, P2)

**What**: Top-talker panels, richer-admin panels, threshold coloring.
**Where**: `control-plane/frontend/src/components/{TopTalkersPanel,BloomFpPanel,CommittedHonoredPanel,FeedStatusPanel}.tsx`, `src/theme/thresholds.ts`, `*.test.tsx`
**Depends on**: T12, T13, T14
**Reuses**: T10 shell, existing panels
**Requirement**: TEL-35, TEL-38 (+ display of 31–34, 36–37)

**Tools**: Skill `coding-guidelines`

**Done when**:
- [ ] Top dst-port + top src (labeled sampled) panels; bloom-FP, committed-honored, feed-status panels; §9.1 threshold coloring (display only, no alert)
- [ ] Vitest unit for each panel from mock payloads
- [ ] Gate passes: `fe`

**Tests**: fe-unit
**Gate**: fe
**Commit**: `feat(telemetry): P2 top-talker and richer-admin panels`

---

### T16: Trend chart + export (CP+FE, P3)

**What**: Time-series over retained windows + CSV/JSON export endpoint.
**Where**: `control-plane/app/api/routers/telemetry.py`, `app/api/schemas/telemetry.py`, `control-plane/frontend/src/components/TrendChart.tsx`, tests
**Depends on**: T9, T12
**Reuses**: retained `TelemetryCounter` windows; existing router
**Requirement**: TEL-39, TEL-40

**Tools**: Skill `coding-guidelines`

**Done when**:
- [ ] `GET …/telemetry/history` (windows range) + CSV/JSON export; `TrendChart` plots retained windows
- [ ] Integration (export) + fe-unit (chart)
- [ ] Gate passes: CP `full` + FE `fe`

**Tests**: integration
**Gate**: full
**Commit**: `feat(telemetry): telemetry trend and export`

---

### T17: Docs + testing conventions [P]

**What**: Document dp `svc_stat`/`dpstat snapshot` conventions, the FE testing gate, and a dashboards README.
**Where**: `.specs/codebase/TESTING.md`, `data-plane/README*`, `control-plane/frontend/README.md`, top-level docs
**Depends on**: T3, T8, T9, T12 (documents the shipped surfaces)
**Reuses**: existing TESTING.md structure
**Requirement**: supports all (traceability/ops)

**Tools**: Skill `docs-writer`

**Done when**:
- [x] TESTING.md gains a data-plane `svc_stat`/`snapshot --json` section + a **frontend** test-type + `fe` gate row
- [x] README/dashboards note (run worker with `worker_telemetry_*`, serve SPA, endpoints)
- [x] Gate passes: docs render review; no code gate

**Tests**: none
**Gate**: none (docs)
**Commit**: `docs(telemetry): dashboards + telemetry testing conventions`

---

## Pre-Approval Validation

### Check 1 — Task Granularity

| Task | Scope | Status |
| --- | --- | --- |
| T1 | 1 capability (per-service counting) across cohesive hot-path files, self-tested | ✅ |
| T2 | loader pin + seed (1 file) | ✅ |
| T3 | 1 dpstat subcommand | ✅ |
| T4 | 1 column + assignment + migration | ✅ |
| T5 | 2 cohesive models + 1 migration | ✅ |
| T6 | 1 reader module | ✅ |
| T7 | 1 aggregator module | ✅ |
| T8 | worker wiring + settings (cohesive) | ✅ |
| T9 | 1 router + schemas | ✅ |
| T10 | SPA scaffold (1 cohesive foundation) | ✅ |
| T11 | 1 dashboard (tenant) | ✅ |
| T12 | 1 admin release surface (dashboard + production SPA serving) | ✅ |
| T13–T16 | 1 P2/P3 slice each | ✅ |
| T17 | docs | ✅ |

### Check 2 — Diagram ↔ Definition Cross-Check

| Task | Depends on (body) | Diagram arrows | Status |
| --- | --- | --- | --- |
| T1 | None | (Phase 1 root) | ✅ |
| T2 | T1 | T1→T2 | ✅ |
| T3 | T1, T2 | T1,T2→T3 | ✅ |
| T4 | None | (Phase 1 root) | ✅ |
| T5 | T4 | T4→T5 | ✅ |
| T6 | None | (Phase 1 root, [P]) | ✅ |
| T7 | T5, T6 | T5,T6→T7 | ✅ |
| T8 | T7 | T7→T8 | ✅ |
| T9 | T5 | T5→T9 | ✅ |
| T10 | None | (Phase 1 root, [P]) | ✅ |
| T11 | T9, T10 | T9,T10→T11 | ✅ |
| T12 | T9, T10, T11 | T9,T10,T11→T12 | ✅ |
| T13 | T3, T7 | T3,T7→T13 | ✅ |
| T14 | T7, T9 | T7,T9→T14 | ✅ |
| T15 | T12, T13, T14 | T12,T13,T14→T15 | ✅ |
| T16 | T9, T12 | T9,T12→T16 | ✅ |
| T17 | T3, T8, T9, T12 | (Phase 8, [P]) | ✅ |

No `[P]` task depends on another `[P]` task in its phase (T6, T10 are independent roots; T17 alone).

### Check 3 — Test Co-location Validation

| Task | Layer created/modified | Matrix requires | Task says | Status |
| --- | --- | --- | --- | --- |
| T1 | DP hot-path verdict/counters | dp-unit | dp-unit | ✅ |
| T2 | DP loader | dp-integration | dp-integration | ✅ |
| T3 | DP dpstat tooling | build+manual | build+manual | ✅ |
| T4 | Models + service | integration | integration | ✅ |
| T5 | Models | integration | integration | ✅ |
| T6 | Pure parse logic (no I/O) | unit | unit | ✅ |
| T7 | Worker aggregator (DB) | integration | integration | ✅ |
| T8 | Worker runtime | integration | integration | ✅ |
| T9 | API router | integration | integration | ✅ |
| T10 | Frontend (new layer) | — (adds `fe`, T17 documents) | fe-unit | ✅* |
| T11 | Frontend | fe (new) | fe-unit | ✅* |
| T12 | Frontend + FastAPI static serving | fe + focused CP unit | fe-unit + cp-unit | ✅* |
| T13 | DP tooling + worker | build + integration | integration | ✅ |
| T14 | Worker + API | integration | integration | ✅ |
| T15 | Frontend | fe (new) | fe-unit | ✅* |
| T16 | API + Frontend | integration + fe | integration | ✅ |
| T17 | Docs | none | none | ✅ |

`*` Frontend is a new layer absent from TESTING.md; T10 introduces the `fe` gate + Vitest convention and T17 records it in TESTING.md. Not a deferral — each FE task ships its own Vitest tests.

---

## Deferred P2/P3 flag

1. **Ringbuf consumer position across processes** — T13 must verify this
   `dpstat tail --json` behavior before implementing top-talkers. Its fallback
   remains a long-lived streaming lane. The `dp_id` sequence, XDP-mode
   `ifindex`, SPA-serving configuration, and M4 #2 `dp_id` contract are
   resolved in the verified P1 implementation.

**MCPs/Skills**: `coding-guidelines` on all code tasks; `docs-writer` on T17; `context7` (fallback web) on T3/T10/T13 for external-API/version checks; `mermaid-studio` already used in Design. Confirm this mapping.

**P1 execution result (2026-07-13):** T1–T12 and T17 are complete. Final
validation is recorded above; TEL-01–30 are verified. T13–T15 remain deferred
to P2 and T16 remains deferred to P3.
