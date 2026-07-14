# Chargeback Metering Tasks

**Design**: `.specs/features/chargeback-metering/design.md` (AD-031)
**Spec**: `.specs/features/chargeback-metering/spec.md` (CHG-01..33)
**Status**: Executed (2026-07-14) — all task commits landed and task-scoped gates pass.

> **Track:** single **control-plane / worker** track — **zero data-plane work** (the byte source is
> telemetry-owned). Per `.specs/codebase/TESTING.md`, only **unit** tasks may be `[P]`; every
> **integration** task serializes on the shared `compose.test.yml` Postgres+Redis, so code-independent
> integration branches (T4-chain vs T7-chain) still run one-at-a-time.
>
> **Execute gate (hard):** *Telemetry & dashboards* must be **executed** first — chargeback reuses its
> `svc_stat_map` `clean_bytes` counter, `dpstat snapshot --json` / `TelemetryReader` / `FakeTelemetryReader`,
> and `ProtectedService.dp_id`. The control-plane slice below is fully buildable/testable against
> `FakeTelemetryReader`; true end-to-end lights up when telemetry (and M4 #2 for real multi-service
> `dp_id`) land. **P2 SPA (T9) additionally gated on the telemetry frontend shell executed.**
>
> **Baselines pinned live at Execute:** control-plane `B_cp = pytest -q` total after telemetry lands
> (≥ 262 agent-worker + feed-sync + telemetry). Each task states the tests it **adds**; cite the new live
> total in its Done-when. Frontend `B_fe` = telemetry SPA's Vitest total.

---

## Execution Plan

### Phase 1 — Pure helpers (Parallel, unit `[P]`)

```
T1 [P]   T2 [P]
```

### Phase 2 — Schema foundation (Sequential, integration)

```
T3
```

### Phase 3 — Meter + API (Sequential integration; two code-independent branches, serialized on shared infra)

```
            ┌─→ T4 ─→ T5 ─→ T6
T3 ─────────┤        ↑ ↑
            └─→ T7 ─→ T8        (T5 also depends on T1, T2)
                 └─→ T9  [gated: telemetry FE]
T1 ─────────────────→ T5
T2 ─────────────────→ T5
```

### Phase 4 — Docs (Parallel, none `[P]`)

```
T6, T7 ─→ T10 [P]
```

---

## Task Breakdown

### T1: `billing_period.py` — UTC calendar-month arithmetic [P]

**What**: Pure helper mapping an instant to its UTC month period `[start, end)` and to the previous period.
**Where**: `control-plane/app/services/billing_period.py` (+ `tests/unit/test_billing_period.py`)
**Depends on**: None
**Reuses**: `app/db/models.py::utc_now` convention (tz-aware UTC); stdlib `datetime` only.
**Requirement**: CHG-09 (period boundary), D-CHG-3 / D-031-6

**Tools**: MCP: NONE · Skill: `coding-guidelines`

**Done when**:
- [x] `month_period(at)` returns tz-aware UTC `(period_start = 1st 00:00:00Z of at's month, period_end = 1st of next month)`; `previous_period(period_start)` returns the prior month.
- [x] Handles month/year rollover (Dec→Jan), leap Feb, and is DST-free (UTC); a non-`"monthly"` `worker_billing_period` is rejected at the call site (documented forward-compat).
- [x] Unit tests cover: mid-month instant, month-end boundary, Dec→Jan rollover, leap-year Feb, `previous_period` inverse.
- [x] Gate check passes: `ruff check . && ruff format --check . && mypy app/ && pytest -q -m unit`
- [x] Test count: `B_cp` unit + ~5 new pass (cite live).

**Tests**: unit · **Gate**: quick
**Commit**: `feat(billing): add UTC calendar-month period helper`

---

### T2: `billing_metrics.py` — nearest-rank p95 + bytes→Gbps [P]

**What**: Pure helpers: nearest-rank 95th percentile and the bytes/sec→Gbps unit conversion.
**Where**: `control-plane/app/services/billing_metrics.py` (+ `tests/unit/test_billing_metrics.py`)
**Depends on**: None
**Reuses**: stdlib `math`, `decimal`; `Numeric(10,2)` quantization convention.
**Requirement**: CHG-09, CHG-10 (p95, billed/overage math), D-031-2

**Done when**:
- [x] `p95_nearest_rank(samples: list[int]) -> int` = value at index `ceil(0.95*n)-1` on ascending sort; `[]` → `0`; single-element → that element.
- [x] `bps_to_gbps(bytes_per_sec: int) -> Decimal` = `Decimal(bytes_per_sec) * 8 / 1_000_000_000` quantized to 2 dp (**the ×8 bytes→bits fix is asserted** so p95 is comparable to gigabit `committed_clean_gbps`).
- [x] Unit tests cover: empty/single/exact-percentile lists, nearest-rank index rounding, and a `bps_to_gbps` case proving ×8 (e.g. `1_250_000_000 B/s → 10.00 Gbps`).
- [x] Gate check passes: `ruff check . && ruff format --check . && mypy app/ && pytest -q -m unit`
- [x] Test count: `B_cp` unit + ~6 new pass (cite live).

**Tests**: unit · **Gate**: quick
**Commit**: `feat(billing): add nearest-rank p95 and bytes-to-Gbps helpers`

---

### T3: `BillingSample` + `BillingUsage` models + migration

**What**: The two additive models (+ `BillingStatus` enum) and their Alembic migration.
**Where**: `control-plane/app/db/models.py`, `control-plane/migrations/versions/<date>_0009_billing.py`
(+ `tests/integration/test_billing_models.py`)
**Depends on**: None
**Reuses**: `TimestampMixin`, `SAEnum(native_enum=False, values_callable=…)`, `Numeric(10,2)`,
`BigInteger`, `UUID(as_uuid=True)` PK, FK `ondelete` idioms; migration head = telemetry's models
migration (down_revision pinned live at Execute); `OveragePolicy` enum reused verbatim.
**Requirement**: CHG-04, CHG-08, CHG-11..16 (schema for series + durable record)

**Tools**: MCP: NONE · Skill: `coding-guidelines`

**Done when**:
- [x] `BillingSample(service_id FK CASCADE NOT NULL, dp_id, sample_ts, clean_bps BigInteger, window_seconds, is_reset, created_at)` with `UNIQUE(service_id, sample_ts)` + `Index(service_id, sample_ts)`.
- [x] `BillingUsage(TimestampMixin; service_id FK SET NULL nullable, tenant_id FK SET NULL nullable, service_name, period_start, period_end, billing_metric, committed/p95/billed/overage_gbps Numeric(10,2), overage_policy, sample_count, status, finalized_at)` with `UNIQUE(service_id, period_start)` + indexes `(tenant_id, period_start)` and `(status, period_end)`.
- [x] `BillingStatus{open,final}` + `billing_status_enum` follow the `native_enum=False` pattern.
- [x] Migration `upgrade`/`downgrade` are reversible; `alembic upgrade head` applies clean on the test DB.
- [x] Integration tests assert: both unique constraints, FK `CASCADE` (sample dies with service), FK `SET NULL` (usage survives service delete), enum round-trip, and that multiple `SET NULL` (service_id=NULL) usage rows for the same `period_start` coexist (NULLs distinct).
- [x] Gate check passes: `ruff check . && ruff format --check . && mypy app/ && pytest -q` (compose up); `alembic upgrade head` on test DB.
- [x] Test count: `B_cp` + ~7 new pass (cite live).

**Tests**: integration · **Gate**: full (+ build: `alembic upgrade head`)
**Commit**: `feat(billing): add BillingSample and BillingUsage models and migration`

---

### T4: `BillingMeter.sample_once` — reader delta + reset + sample upsert

**What**: The sampler half of the meter: read the snapshot, compute per-service clean-bps deltas with reset
detection, upsert idempotent `BillingSample` rows; maintain the `dp_id → (service, tenant, plan)` cache.
**Where**: `control-plane/app/worker/billing.py` (new) (+ `tests/integration/test_billing_meter.py`)
**Depends on**: T3
**Reuses**: telemetry `TelemetryReader` / `FakeTelemetryReader` (inject; `snapshot()`), `session_scope`,
`ProtectedService.dp_id` + `ServicePlan` join, the aggregator reset-detection shape (Δ<0 or
`active.version` changed).
**Requirement**: CHG-01..07

**Tools**: MCP: NONE · Skill: `coding-guidelines`

**Done when**:
- [x] `sample_once()` reads `reader.snapshot()`; `None` (offline) → skip, no fabricated sample.
- [x] Per active `dp_id`: `clean_bps = (clean_bytes − prev[dp_id]) // elapsed`; reset (Δ<0 or version change) → `is_reset=True`, use post-reset value, never negative; first tick seeds `prev` and emits no sample.
- [x] Services with no counter this interval get an explicit `0`-bps sample; unknown/deleted `dp_id` ignored.
- [x] Upsert is idempotent on `(service_id, sample_ts)`.
- [x] Integration tests (with `FakeTelemetryReader`): delta correctness, reset→no-negative, zero-bps sample, first-tick baseline skip, unknown-dp_id ignored, idempotent re-run.
- [x] Gate check passes: `ruff check . && ruff format --check . && mypy app/ && pytest -q`
- [x] Test count: `B_cp` + ~6 new pass (cite live).

**Tests**: integration · **Gate**: full
**Commit**: `feat(billing): sample per-service clean-bps with reset detection`

---

### T5: `BillingMeter` rollup — p95 → open estimate + finalize

**What**: The rollup half: refresh the running `open` `BillingUsage` estimate and finalize due/orphaned
periods into immutable `final` rows.
**Where**: `control-plane/app/worker/billing.py` (modify) (+ extend `test_billing_meter.py`)
**Depends on**: T1, T2, T4
**Reuses**: `billing_period.month_period` (T1), `p95_nearest_rank` + `bps_to_gbps` (T2), `session_scope`,
the plan snapshot from T4's cache.
**Requirement**: CHG-09..18

**Tools**: MCP: NONE · Skill: `coding-guidelines`

**Done when**:
- [x] `refresh_open_periods()`: ensure an `open` `BillingUsage` for `month_period(now)` per active service; set `p95_clean_gbps = bps_to_gbps(p95_nearest_rank(period samples))`, `billed_gbps = max(committed, p95)`, `overage_gbps = max(0, p95 − committed)`, `sample_count`; snapshot `committed_clean_gbps`/`billing_metric`/`overage_policy`.
- [x] `finalize_due_periods()`: flip `open→final` (+ `finalized_at`) where `period_end ≤ now` **or** `service_id IS NULL` (deleted); idempotent (skip `final`); `UNIQUE(service_id, period_start)` blocks dups.
- [x] Zero-sample period → p95=0, billed=committed (floor); `capped`/`billed` both store `overage_gbps`.
- [x] Integration tests: p95→`billed=max(committed,p95)`, overage, open-estimate refresh, finalize at boundary (immutable), orphan (service-deleted) finalize, zero-sample committed floor, idempotent re-run, committed-snapshot-at-close.
- [x] Gate check passes: `ruff check . && ruff format --check . && mypy app/ && pytest -q`
- [x] Test count: `B_cp` + ~8 new pass (cite live).

**Tests**: integration · **Gate**: full
**Commit**: `feat(billing): roll up p95 into open and finalized BillingUsage`

---

### T6: `BillingMeter` prune + `run_loop` + worker wiring + settings

**What**: Sample pruning, the background `run_loop`, worker-process integration, and `worker_billing_*`
settings.
**Where**: `control-plane/app/worker/billing.py` (modify), `app/worker/worker.py`,
`app/worker/__main__.py`, `app/core/config.py` (+ `tests/integration/test_worker_runtime.py` extension,
`tests/unit/test_worker_backoff.py` if a pure knob helper is added)
**Depends on**: T5
**Reuses**: the `Worker.run` spawn/await/cancel lane lifecycle (`FeedCoordinator` / telemetry
`run_loop` precedent), `worker_*` settings convention, `session_scope`.
**Requirement**: CHG-08 (prune), CHG-07/CHG-18 (bounded degrade), worker lane (A-CHG-2)

**Tools**: MCP: NONE · Skill: `coding-guidelines`

**Done when**:
- [x] `prune_samples()` deletes `BillingSample` for finalized periods older than `worker_billing_sample_retention_days`.
- [x] `run_loop(stop)`: `tick()` (sample→refresh→finalize→prune, each own `session_scope`) then interruptible sleep `worker_billing_interval_seconds`; catch-log-continue on any error (never crashes the worker).
- [x] `Worker.__init__(billing=None)`; `run()` spawns `run_loop(stop_event)` before the loop and awaits/cancels in `finally` alongside existing lanes; `__main__` builds `TelemetryReader`+`BillingMeter` and injects when `worker_billing_enabled`.
- [x] `Settings`: `worker_billing_enabled=True`, `worker_billing_interval_seconds=Field(300.0, gt=0)`, `worker_billing_sample_retention_days=Field(400, gt=0)`, `worker_billing_period: Literal["monthly"]="monthly"` (reuses telemetry reader knobs; no new reader settings).
- [x] Integration tests: prune removes only finalized-old samples; `run_loop` survives a raising tick and continues; worker spawns/cancels the lane cleanly on stop.
- [x] Gate check passes: `ruff check . && ruff format --check . && mypy app/ && pytest -q`
- [x] Test count: `B_cp` + ~5 new pass (cite live).

**Tests**: integration · **Gate**: full
**Commit**: `feat(billing): run the metering lane in the worker with pruning and settings`

---

### T7: `/billing` router — usage read + export

**What**: The `/billing/usage` (tenant/admin, 404 cross-tenant, empty state) and `/billing/usage/export`
(admin CSV/JSON, finalized only) endpoints + schemas.
**Where**: `control-plane/app/api/routers/billing.py` (new), `app/api/schemas/billing.py` (new),
`app/api/main.py` (register) (+ `tests/integration/test_billing_api.py`)
**Depends on**: T3
**Reuses**: `core/deps.py` (`get_current_user`, `require_admin`, `load_service_for_principal`→404,
`Principal.tenant_id`), `routers/services.py` router/schema `Annotated[..., Depends]` conventions,
`billing_period.month_period` for the `period` (`YYYY-MM`) filter, `StreamingResponse` for CSV.
**Requirement**: CHG-19..28

**Tools**: MCP: NONE · Skill: `coding-guidelines`

**Done when**:
- [x] `GET /billing/usage?service_id=&period=&status=`: tenant → rows where `tenant_id == principal.tenant_id`; with `service_id` → `load_service_for_principal` (404 cross-tenant); admin → all, filterable; `open` rows marked provisional; empty → `200 {usage:[], has_data:false}`.
- [x] `GET /billing/usage/export?period=&format=csv|json`: `require_admin`; **finalized** rows only; CSV via `StreamingResponse` (service, tenant, period, committed, p95, billed, overage, overage_policy, sample_count); open periods omitted/marked provisional.
- [x] Read-only (no mutation); Gbps serialized as `Numeric(10,2)` Decimal.
- [x] Integration tests (AsyncClient): tenant own, 404 cross-tenant, admin all, empty state, export CSV + JSON, open-provisional labeling.
- [x] Gate check passes: `ruff check . && ruff format --check . && mypy app/ && pytest -q`
- [x] Test count: `B_cp` + ~7 new pass (cite live).

**Tests**: integration · **Gate**: full
**Commit**: `feat(billing): add /billing usage read and export endpoints`

---

### T8: `/billing/usage/history` endpoint (P3)

**What**: Finalized-period history endpoint.
**Where**: `control-plane/app/api/routers/billing.py` (modify), `app/api/schemas/billing.py` (modify)
(+ extend `test_billing_api.py`)
**Depends on**: T7
**Reuses**: T7 router/schemas; tenant/admin scoping from T7.
**Requirement**: CHG-33

**Tools**: MCP: NONE · Skill: `coding-guidelines`

**Done when**:
- [x] `GET /billing/usage/history?service_id=&limit=` returns finalized periods (newest-first) with tenant scoping (404 cross-tenant on `service_id`).
- [x] Integration tests: history list + ordering + isolation.
- [x] Gate check passes: `ruff check . && ruff format --check . && mypy app/ && pytest -q`
- [x] Test count: `B_cp` + ~2 new pass (cite live).

**Tests**: integration · **Gate**: full
**Commit**: `feat(billing): add finalized-period history endpoint`

---

### T9: Billing showback SPA panel (P2 — gated on telemetry frontend)

**What**: A `BillingPanel` view/route (tenant billed-vs-committed + finalized list; admin node-wide + overage
flag; open = provisional) reusing the telemetry SPA shell.
**Where**: `control-plane/frontend/` — new `BillingPanel` + route + `useBillingUsage` query (+ its Vitest
tests)
**Depends on**: T7 (+ **telemetry SPA shell executed**)
**Reuses**: telemetry SPA shell (auth/`/auth/me` role routing, `AppLayout`, TanStack Query polling,
`api/client.ts`, chart components), D-TEL-2.
**Requirement**: CHG-29..31

**Tools**: MCP: NONE · Skill: `coding-guidelines`

**Done when**:
- [x] Tenant view: per-service current running billed-Gbps vs committed + finalized-period list; admin view: node/tenant-wide billed vs committed with `overage_gbps` flagged; open periods labeled **provisional**.
- [x] Tenant never sees another tenant's usage (API-enforced; UI honors it).
- [x] Vitest component tests for the panel states (loading/empty/data/provisional).
- [x] Gate check passes: `fe` gate — `npm run lint && npm run typecheck && npm run test --run && npm run build`.
- [x] Test count: `B_fe` + new pass (cite live).

**Tests**: fe (frontend — telemetry-established layer) · **Gate**: fe
**Commit**: `feat(billing): add billing showback panel to the SPA`

---

### T10: Docs — TESTING.md billing layers + README/worker notes [P]

**What**: Document the billing worker lane, the two new test layers, the p95/units convention, and the
`/billing` surface.
**Where**: `.specs/codebase/TESTING.md`, control-plane `README`/worker docs
**Depends on**: T6, T7
**Reuses**: existing TESTING.md structure (Coverage Matrix rows, conventions section).
**Requirement**: cross-cutting (documents CHG-01..28)

**Tools**: MCP: NONE · Skill: `docs-writer`

**Done when**:
- [x] TESTING.md Coverage Matrix gains `app/worker/billing.py` (integration), `app/services/billing_period.py` / `billing_metrics.py` (unit), `app/api/routers/billing.py` (integration).
- [x] A "Billing conventions" note records: nearest-rank p95, bytes/sec→Gbps ×8, UTC calendar-month periods, `open→final` immutability, `FakeTelemetryReader` reuse for meter tests.
- [x] Gate check passes: `python -c "import app.main"` (import smoke — confirms no code drift).
- [x] No test count change (docs only).

**Tests**: none · **Gate**: build
**Commit**: `docs(billing): document billing test layers and metering conventions`

---

## Execution Record (2026-07-14)

| Task | Commit | Verification |
| --- | --- | --- |
| T1 | `9a8d1f8` | Quick gate: 120 unit passed; 344 deselected. |
| T2 | `965c1ec` | Quick gate: 120 unit passed; 344 deselected. |
| T3 | `2a08aa6` | 7 billing-model tests, ruff, format, mypy, and Alembic head upgrade passed. |
| T4 | `33419cb` | 7 billing-meter sampling tests, ruff, format, and mypy passed. |
| T5 | `9a4520a` | 15 billing-meter rollup/finalization tests, ruff, format, and mypy passed. |
| T6 | `ab4d05b` | 24 meter/runtime focused tests, ruff, format, and mypy passed. |
| T7 | `6a980d7` | 6 billing API tests, ruff, format, and mypy passed. |
| T8 | `07a31c6` | 8 billing API tests, ruff, format, and mypy passed. |
| T9 | `1166a3a` | Frontend lint, typecheck, 18 Vitest tests, and production build passed. |
| T10 | `ffd2496` | `python -c "import app.main"` import smoke passed. |

### Final targeted verification

- Control plane: `ruff check .`, `ruff format --check .`, and `mypy app/` passed
  (149 files formatted; 68 mypy sources).
- Billing-focused control-plane suite: 50 passed across the period, metrics,
  models, meter, API, and worker-runtime tests.
- Frontend: lint, typecheck, 18 Vitest tests, and the production Vite build passed.

### Repository-wide full-suite note

Several required `pytest -q` attempts reached the repository's known
order/isolation/reporting issue: the tool detached before the terminal summary,
and the existing `test_global_deny_applier.py` last-failed entries remained.
The billing-focused suite above ran cleanly in one serialized process; no
unrelated global-deny or allocation/auth tests were modified for this feature.

---

## Pre-Approval Validation (all three checks)

### Check 1 — Task Granularity

| Task | Scope | Status |
| --- | --- | --- |
| T1 `billing_period.py` | 1 pure module (2 fns) | ✅ Granular |
| T2 `billing_metrics.py` | 1 pure module (2 fns) | ✅ Granular |
| T3 models + migration | 2 cohesive additive models + enum + 1 migration (one schema unit) | ✅ Cohesive |
| T4 `sample_once` | 1 method group (sampler) | ✅ Granular |
| T5 rollup | 1 method group (refresh+finalize) | ✅ Granular |
| T6 prune+run_loop+wiring | 1 concern (run the lane in the worker) | ✅ Cohesive |
| T7 `/billing` usage+export | 1 router (2 endpoints, same file) | ✅ Cohesive |
| T8 history endpoint | 1 endpoint | ✅ Granular |
| T9 SPA panel | 1 view + query | ✅ Granular |
| T10 docs | doc edits | ✅ Granular |

### Check 2 — Diagram ↔ Definition Cross-Check

| Task | Depends on (body) | Diagram arrows in | Status |
| --- | --- | --- | --- |
| T1 | None | (Phase 1 root) | ✅ |
| T2 | None | (Phase 1 root) | ✅ |
| T3 | None | (Phase 2 root) | ✅ |
| T4 | T3 | T3→T4 | ✅ |
| T5 | T1, T2, T4 | T1→T5, T2→T5, T4→T5 | ✅ |
| T6 | T5 | T5→T6 | ✅ |
| T7 | T3 | T3→T7 | ✅ |
| T8 | T7 | T7→T8 | ✅ |
| T9 | T7 (+telemetry FE) | T7→T9 | ✅ |
| T10 | T6, T7 | T6→T10, T7→T10 | ✅ |

No `[P]` task depends on another `[P]` task in its phase (T1⊥T2; T10 alone). ✅

### Check 3 — Test Co-location (vs TESTING.md Coverage Matrix)

| Task | Code layer | Matrix requires | Task says | Status |
| --- | --- | --- | --- | --- |
| T1 | `services/billing_period.py` (pure logic) | unit | unit | ✅ |
| T2 | `services/billing_metrics.py` (pure logic) | unit | unit | ✅ |
| T3 | `db/models.py` + migration | integration | integration | ✅ |
| T4 | `worker/billing.py` (DB/reader) | integration | integration | ✅ |
| T5 | `worker/billing.py` | integration | integration | ✅ |
| T6 | `worker/{billing,worker,__main__}.py` (runtime) + config (none) | integration (highest) | integration | ✅ |
| T7 | `api/routers/billing.py` | integration | integration | ✅ |
| T8 | `api/routers/billing.py` | integration | integration | ✅ |
| T9 | `frontend/` (FE layer, telemetry-established `fe`) | fe | fe | ✅ |
| T10 | docs (TESTING.md/README) | none | none | ✅ |

All three checks pass — no ❌.

**Parallelism note:** only **T1/T2** (unit) and **T10** (none) are `[P]`. T3–T8 are integration → they
**serialize** on `compose.test.yml` even where code-independent (T7 does not depend on the T4→T6 chain, but
runs sequentially with it). T9 is a separate frontend toolchain, gated on the telemetry SPA.

---

## Tools per task (precedent: `coding-guidelines` for code, `docs-writer` for docs)

No MCPs (Context7 recorded unavailable in prior sessions; nothing here needs external API lookup — all
grounded in-repo + approved AD-030). All code tasks use the `coding-guidelines` skill; T10 uses
`docs-writer`. Override this assignment if you prefer different tooling.

---

## Requirement Coverage

| Requirement | Task(s) |
| --- | --- |
| CHG-01..07 (sample series) | T4 (+ T2 units, T3 model) |
| CHG-08 (prune) | T6 |
| CHG-09..18 (p95 rollup & BillingUsage) | T5 (+ T1 period, T2 p95/units, T3 model) |
| CHG-19..25 (usage API) | T7 |
| CHG-26..28 (export) | T7 |
| CHG-29..31 (SPA showback, P2) | T9 |
| CHG-32..33 (history/trend, P3) | T8 (API), T9 (chart) |

All 33 requirements mapped. **Next: approve → Execute** (Phase 1: T1 + T2 `[P]`; then T3; then the T4→T6
and T7→T8 integration chains serialized; T9 when the telemetry SPA is up; T10 `[P]` at the end).
