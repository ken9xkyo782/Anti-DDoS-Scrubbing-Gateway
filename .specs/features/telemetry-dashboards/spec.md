# Telemetry & Dashboards Specification

**Feature ID prefix:** `TEL`
**Milestone:** M5 — Observability & chargeback (first of two features; sibling = *Chargeback metering*)
**Status:** P1 verified (2026-07-13); P2 in progress; P3 pending

## Problem Statement

The gateway enforces a full L3/L4 verdict pipeline (M2–M3) and syncs config via the worker (M4), but operators and tenants are blind: the only way to see what the data-plane is doing is the operator-local `dpstat` CLI on the node. Tenants cannot see whether their traffic is clean or dropped, and admins have no node-wide view of health (XDP mode, map version, `map_error`, worker/feed status). This feature makes the data-plane observable end-to-end — surfacing per-service and node-level metrics to a realtime web dashboard — which is also the precondition for M5 chargeback and M6 alerting.

## Goals

- [x] Tenant can see their own service's clean-vs-drop packets/bytes, drop-reason distribution, and current PPS/BPS, refreshing **≤ 2 s** (PROJECT goal, TDD §11).
- [x] Admin can see node-level aggregates plus a health snapshot: XDP mode (native/generic/off), active map version, apply status, `map_error`, worker backlog, feed status, throughput-vs-capacity.
- [x] Data-plane emits **exact per-CPU per-service** clean + per-drop-reason packet/byte counters on the hot path — the exact clean-byte counter becomes the billing source of truth reused by *Chargeback metering*.
- [x] Strict tenant isolation on all telemetry reads (5.2): zero cross-tenant leakage.
- [x] Bootstrap the React SPA (auth/session, role-aware routing, layout) — the first frontend in the project.

## Out of Scope

Explicitly excluded. Documented to prevent scope creep.

| Feature | Reason |
| --- | --- |
| p95 clean-Gbps computation, `BillingUsage`, overage policy | Sibling M5 feature *Chargeback metering*; this feature only provides the exact per-service clean-byte counter it consumes |
| Alert firing — email/webhook, severity routing, threshold+hysteresis, dedup, auto-resolve | M6 *Alerting*; telemetry provides the counters/thresholds it reads, and may **display** a threshold breach, but fires nothing |
| Global bypass / maintenance-mode toggle + "BYPASS ACTIVE" banner | M6 *Bypass & maintenance mode* (the flag/state does not exist until then) |
| Config CRUD screens in the SPA (services/rules/whitelist/blacklist/feeds) | Separate later frontend effort; this feature ships the SPA shell + telemetry/health views only (D-TEL-2) |
| Server-push transport (SSE/WebSocket) | REST polling chosen (D-TEL-3); revisit only if ≤2s cannot be met |
| Added-latency p99 metric | No in-band latency-measurement mechanism exists in v1; deferred (revisit with a dedicated probe) |
| Structured JSON log pipeline / external log or TSDB aggregation | M6 logging; v1 keeps bounded rolling windows in Postgres |
| Per-source-IP retention / anonymization policy for `top_src` | Pilot accepts raw source IP (CM-08); GA adds retention/anonymization |

---

## User Stories

### P1: Per-service hot-path counters (data-plane) ⭐ MVP

**User Story**: As the telemetry pipeline, I want exact per-service packet/byte counters emitted by XDP so that per-service metrics (and later billing) have an authoritative source.

**Why P1**: Tenant per-service telemetry (§9.1) is impossible today — only node-global counters exist. Everything above depends on this. (D-TEL-1)

**Acceptance Criteria**:

1. WHEN a packet is redirected clean `IN→OUT` for a matched service THEN the data-plane SHALL increment that service's per-CPU **clean packet** and **clean byte** (frame length) counters exactly.
2. WHEN a packet is dropped for a matched service THEN the data-plane SHALL increment that service's per-CPU **drop packet** and **drop byte** counters AND the per-service per-reason counter for the resolved §9.2 `drop_reason`.
3. WHEN a packet is dropped/passed before a service is matched (e.g. `service_miss`, malformed, IPv6) THEN it SHALL be accounted to the existing node-global `counter_map` only (no `service_id`) — per-service counters exist only for matched services.
4. WHEN counters are incremented THEN it SHALL be lock-free per-CPU with no added hot-path allocation; the clean-byte counter SHALL be exact (billing-grade, decoupled from sampled events).
5. WHEN the BPF program is reloaded THEN the per-service counters SHALL reset to zero (documented; the aggregator handles the delta reset — see TEL-09).
6. WHEN a service is deleted THEN its stale per-service counter entry SHALL be reconciled/evicted by the aggregator; the per-service counter map SHALL be bounded to the service scale envelope (1,000 services).

**Independent Test**: `BPF_PROG_TEST_RUN` — inject a clean packet and a dropped packet for a seeded service, read the per-service counter map, assert exact pkt/byte and per-reason increments; assert pre-service drops touch only node-global counters.

---

### P1: Telemetry aggregation pipeline (worker) ⭐ MVP

**User Story**: As an admin, I want the worker to roll data-plane counters into windowed database rows so that the API and dashboard have fresh, queryable metrics.

**Why P1**: The bridge from kernel maps to the web tier. Without it there is nothing to serve.

**Acceptance Criteria**:

1. WHEN the worker is running THEN it SHALL run a periodic `TELEMETRY_AGGREGATE` job at a configurable interval **≤ 2 s** via the in-worker scheduler (same pattern as feed-sync due-time scheduling).
2. WHEN an aggregation window runs THEN it SHALL read the pinned data-plane maps (per-service counters, node `counter_map`, `bloom_stats`, `sample_stats`) reusing the established C-helper subprocess pattern, and compute per-window **deltas** since the previous window.
3. WHEN a counter delta is negative (reload/reset detected) THEN the aggregator SHALL treat it as a reset and record the raw post-reset value rather than a negative delta.
4. WHEN aggregation completes THEN it SHALL persist `TelemetryCounter` rows keyed by `(service_id | node, window_ts)` carrying clean/drop packets+bytes, per-reason drop counts, and derived PPS/BPS, plus `window_seconds`.
5. WHEN aggregation reads the drop-event ring buffer samples THEN it SHALL maintain rolling top-N **dst-ports** and top-N **src IPs** per service and node (sampled/approximate, D-TEL-4).
6. WHEN a window runs THEN it SHALL capture a node health snapshot: XDP mode (native/generic/off), active map version, apply status/backlog, `map_error` count, feed-sync status, node clean-BPS vs `node_clean_capacity`.
7. WHEN aggregation is re-run for the same `window_ts` THEN it SHALL be idempotent (no double counting); a failed or late window SHALL NOT corrupt prior windows.
8. WHEN Postgres or the data-plane maps are unavailable THEN the aggregator SHALL degrade bounded (skip the window, log, resume next tick) without crashing the worker.
9. WHEN telemetry rows exceed a configurable retention horizon THEN old windows SHALL be pruned to bound table growth.

**Independent Test**: Run the worker against seeded pinned maps (or a fake map reader); assert `TelemetryCounter` rows appear within the interval with correct deltas; kill DB mid-run and assert the worker survives and resumes.

---

### P1: Telemetry & health API (control-plane) ⭐ MVP

**User Story**: As a tenant/admin, I want REST endpoints for my telemetry so that the dashboard can render it.

**Why P1**: The read surface the SPA polls; the isolation boundary that keeps tenants apart.

**Acceptance Criteria**:

1. WHEN a `tenant_user` GETs `/services/{id}/telemetry` for a service they own THEN the API SHALL return the latest window's clean/drop packets+bytes, drop-reason distribution, and PPS/BPS for that service.
2. WHEN a `tenant_user` requests telemetry for a service they do not own THEN the API SHALL fail-closed (404/403) — strict tenant-ownership check (5.2), same guard pattern as existing routers.
3. WHEN an `admin` GETs the node telemetry/health endpoint THEN the API SHALL return node-level aggregates plus the health snapshot (XDP mode, map version, apply status, `map_error`, worker backlog, feed status, throughput vs capacity).
4. WHEN telemetry is returned THEN it SHALL include `window_ts` and `window_seconds` so clients can render freshness/staleness.
5. WHEN no telemetry exists yet (fresh service, or worker has not aggregated) THEN the API SHALL return a zeroed/empty payload with an explicit "no data yet" marker, not an error.
6. WHEN any telemetry endpoint is called THEN it SHALL be read-only, create no mutation, and pass through the existing session/RBAC middleware.

**Independent Test**: Seed `TelemetryCounter` rows; assert owner tenant gets data, non-owner gets 404/403, admin gets node view; assert empty-state returns 200 with the no-data marker.

---

### P1: Dashboard SPA — tenant service view + admin node view ⭐ MVP

**User Story**: As a tenant/admin, I want a web dashboard that shows my metrics live so that I can see what the gateway is doing to my traffic.

**Why P1**: The user-facing payoff; "dashboards" is half the feature name. Bootstraps the React SPA.

**Acceptance Criteria**:

1. WHEN the project builds the frontend THEN it SHALL bootstrap a React SPA with session login against `/auth/login`, role-aware routing (tenant vs admin), and a base layout.
2. WHEN a tenant logs in THEN the SPA SHALL list their services and render a per-service telemetry view (clean vs drop, drop-reason distribution, PPS/BPS).
3. WHEN an admin logs in THEN the SPA SHALL render a node view (node totals + health: XDP mode, map version, apply status, `map_error`, backlog, feed status, throughput vs capacity).
4. WHEN a telemetry view is open THEN the SPA SHALL poll the API every **≤ 2 s** and update the view in place (no full reload).
5. WHEN the latest window is stale (older than a staleness threshold) or the worker is down THEN the SPA SHALL show a staleness / "no fresh data" indicator instead of silently showing old numbers.
6. WHEN the XDP mode is `generic` or `off` THEN the admin view SHALL visually flag it as critical.
7. WHEN a tenant is authenticated THEN the SPA SHALL never display another tenant's services or telemetry (API-enforced; UI honors it).
8. WHEN a view is loading, empty, or errors THEN the SPA SHALL render graceful loading/empty/error states.
9. WHEN the session expires THEN the SPA SHALL redirect to login.

**Independent Test**: Browser walkthrough — log in as tenant, watch a service view update ≤2s while traffic runs; log in as admin, see node health + XDP-mode flag; confirm a tenant cannot reach another tenant's service view.

---

### P2: Richer admin observability

**User Story**: As an admin, I want the deeper health/fairness signals so that I can judge node behavior under attack.

**Why P2**: Valuable operationally but not required to prove the end-to-end slice.

**Acceptance Criteria**:

1. WHEN the admin node view renders THEN it SHALL show bloom hit / false-positive metrics (`bloom_stats` + `bloom_hit_lpm_miss`).
2. WHEN a service has a `ServicePlan` THEN the admin/tenant view SHALL show "committed honored" — actual clean BPS vs `committed_clean_gbps` (fairness signal).
3. WHEN the admin node view renders THEN it SHALL show worker job backlog depth and last apply status detail.
4. WHEN feed sources exist THEN the admin node view SHALL show per-source last feed-sync status.
5. WHEN a metric exceeds its §9.1 threshold THEN the view SHALL visually indicate the breach (display only — no alert is fired; alerting is M6).

**Independent Test**: Seed bloom/backlog/feed/fairness state; assert each panel renders the expected value and threshold coloring.

---

### P2: Top-talkers view (sampled)

**User Story**: As a tenant/admin, I want to see the top attacking ports and source IPs so that I understand the attack shape.

**Why P2**: Additive attack visibility on top of the core counters; sampled, not exact.

**Acceptance Criteria**:

1. WHEN a service/node telemetry view renders THEN it SHALL show top-N **dst-ports** aggregated from the sampled ring buffer.
2. WHEN a service/node telemetry view renders THEN it SHALL show top-N **src IPs** (sampled), labeled as approximate.
3. WHEN `top_src` is displayed THEN the UI SHALL treat it under the pilot PII posture (CM-08) — no retention/anonymization guarantees in v1.

**Independent Test**: Feed synthetic sampled drop events; assert top-N ports/IPs ordering; assert the "sampled/approximate" label is present.

---

### P3: Historical trend & export

**User Story**: As an admin/tenant, I want a short time-series and export so that I can look back over recent windows.

**Why P3**: Nice-to-have; latest-window views satisfy the MVP.

**Acceptance Criteria**:

1. WHEN a telemetry view is open THEN it SHALL render a rolling time-series chart over the retained windows.
2. WHEN a user requests export THEN the API SHALL return a service's/node's retained windows as CSV/JSON.

**Independent Test**: Retain N windows; assert the chart plots them and export returns matching rows.

---

## Edge Cases

- WHEN a per-CPU counter wraps or the program reloads THEN the aggregator SHALL detect the reset (negative delta) and record the raw value (TEL-09).
- WHEN a service is deleted mid-window THEN its stale counter entry SHALL be evicted and not attributed to a live service (TEL-06).
- WHEN the worker is down THEN telemetry goes stale; the API returns the last window with its `window_ts`, and the SPA flags staleness (TEL-26) — it SHALL NOT present stale numbers as live.
- WHEN the gateway BPF program is not loaded THEN node health SHALL report XDP mode `off` and per-service telemetry SHALL be empty (no crash).
- WHEN a tenant has zero services THEN the tenant view SHALL render an empty state, not an error.
- WHEN more than 1,000 services exist THEN the per-service counter map SHALL bound (evict/skip beyond envelope) without corrupting existing entries.
- WHEN an aggregation window is skipped (DB/map outage) THEN the next window SHALL resume; freshness degrades but no data corrupts (TEL-14 degrade + TEL-13 idempotent).
- WHEN a map swap (double-buffer flip) occurs mid-window THEN counter reads SHALL remain consistent (per-service counters are unslotted runtime state, untouched by the swap).

---

## Requirement Traceability

| Requirement ID | Story | Phase | Status |
| --- | --- | --- | --- |
| TEL-01 | P1: Per-service counters | - | Verified |
| TEL-02 | P1: Per-service counters | - | Verified |
| TEL-03 | P1: Per-service counters | - | Verified |
| TEL-04 | P1: Per-service counters | - | Verified |
| TEL-05 | P1: Per-service counters | - | Verified |
| TEL-06 | P1: Per-service counters | - | Verified |
| TEL-07 | P1: Aggregation pipeline | - | Verified |
| TEL-08 | P1: Aggregation pipeline | - | Verified |
| TEL-09 | P1: Aggregation pipeline | - | Verified |
| TEL-10 | P1: Aggregation pipeline | - | Verified |
| TEL-11 | P1: Aggregation pipeline | - | Verified |
| TEL-12 | P1: Aggregation pipeline | - | Verified |
| TEL-13 | P1: Aggregation pipeline | - | Verified |
| TEL-14 | P1: Aggregation pipeline | - | Verified |
| TEL-15 | P1: Aggregation pipeline | - | Verified |
| TEL-16 | P1: Telemetry & health API | - | Verified |
| TEL-17 | P1: Telemetry & health API | - | Verified |
| TEL-18 | P1: Telemetry & health API | - | Verified |
| TEL-19 | P1: Telemetry & health API | - | Verified |
| TEL-20 | P1: Telemetry & health API | - | Verified |
| TEL-21 | P1: Telemetry & health API | - | Verified |
| TEL-22 | P1: Dashboard SPA | - | Verified |
| TEL-23 | P1: Dashboard SPA | - | Verified |
| TEL-24 | P1: Dashboard SPA | - | Verified |
| TEL-25 | P1: Dashboard SPA | - | Verified |
| TEL-26 | P1: Dashboard SPA | - | Verified |
| TEL-27 | P1: Dashboard SPA | - | Verified |
| TEL-28 | P1: Dashboard SPA | - | Verified |
| TEL-29 | P1: Dashboard SPA | - | Verified |
| TEL-30 | P1: Dashboard SPA | - | Verified |
| TEL-31 | P2: Richer admin observability | - | Pending |
| TEL-32 | P2: Richer admin observability | - | Pending |
| TEL-33 | P2: Richer admin observability | - | Pending |
| TEL-34 | P2: Richer admin observability | - | Pending |
| TEL-35 | P2: Richer admin observability | - | Pending |
| TEL-36 | P2: Top-talkers view | - | Verified |
| TEL-37 | P2: Top-talkers view | - | Verified |
| TEL-38 | P2: Top-talkers view | - | Pending |
| TEL-39 | P3: Historical trend & export | - | Pending |
| TEL-40 | P3: Historical trend & export | - | Pending |

**ID format:** `TEL-[NUMBER]`

**Status values:** Pending → In Design → In Tasks → Implementing → Verified

**Coverage:** 40 total. TEL-01..30 are implemented and verified by the P1
gates. T13 verifies TEL-36..37; TEL-31..35 and TEL-38 remain pending for P2,
and TEL-39..40 remain pending for P3.

---

## Success Criteria

How we know the feature is successful:

- [x] A tenant sees their own service's clean-vs-drop + drop-reason distribution + PPS/BPS, refreshing ≤ 2 s.
- [x] An admin node view shows XDP mode, active map version, `map_error`, apply status, worker backlog, and feed status.
- [x] The per-service clean-byte counter is exact and reconciles to node-level clean bytes within counter-reset tolerance (billing-grade for chargeback reuse).
- [x] Tenant isolation holds: a tenant cannot read another tenant's telemetry via API or SPA (zero leakage in the test set).
- [x] Data-plane counter → visible-in-dashboard latency ≤ 2 s under nominal operation.
- [x] The data-plane test suite baseline stays green with the new per-service counters wired.
