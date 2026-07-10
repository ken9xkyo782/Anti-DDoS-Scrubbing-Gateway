# Chargeback Metering Specification

**Feature ID prefix:** `CHG`
**Milestone:** M5 — Observability & chargeback (second of two features; sibling = *Telemetry & dashboards*)
**Status:** Spec drafted

## Problem Statement

The commercial model is internal chargeback: tenants pay for the clean bandwidth the gateway
delivers, metered by **p95 clean Gbps** per period (PROJECT / TDD §4.8). The data-plane already
produces an exact per-CPU clean-byte count (introduced by *Telemetry & dashboards*, D-TEL-1), and
`ServicePlan` already carries `committed_clean_gbps`, `ceiling_clean_gbps`, `billing_metric`, and
`overage_policy` — but nothing turns those bytes into a billable figure. There is no p95 computation,
no `BillingUsage` record, and no export for the internal finance system. This feature closes M5 by
metering clean throughput into immutable per-period `BillingUsage` records and exposing them for
chargeback/showback.

## Goals

- [ ] Compute **p95 clean Gbps** per service over a billing period from the **exact** per-CPU clean-byte
      counter (billing-grade, decoupled from sampled events — AD-006 / PROJECT constraint).
- [ ] Produce an immutable per-service `BillingUsage` per **billing period** (default monthly) with
      `billed_gbps = max(committed_clean_gbps, p95_clean_gbps)` and `overage_gbps` (TDD §4.8).
- [ ] Expose `GET /billing/usage` with strict tenant isolation (tenant = own services; admin = node-wide).
- [ ] Export finalized usage (CSV/JSON) for the internal chargeback/finance system.
- [ ] Keep a running current-period estimate for showback, distinct from settled/finalized rows.

## Out of Scope

Explicitly excluded. Documented to prevent scope creep.

| Feature | Reason |
| --- | --- |
| The exact per-service `clean_bytes` hot-path counter | Introduced by sibling *Telemetry & dashboards* (D-TEL-1); chargeback **reuses** it, adds no new data-plane counter |
| Per-service telemetry windows, drop-reason distribution, node health, top-talkers | *Telemetry & dashboards*; chargeback consumes only the clean-byte source, on its own coarse cadence |
| Billing metrics other than `p95_clean_bps` (e.g. 95th-of-sum, average, peak) | v1 implements only `p95_clean_bps`; the `billing_metric` column stays forward-compat (A-CHG-7) |
| Bypass-mode "counted separately" accounting (TDD §4.9) | Global bypass / maintenance mode is M6; the flag does not exist yet. When it lands, bypass clean must be excluded from billing (forward dependency, A-CHG-8) |
| Invoicing, currency, price rates, showback statements | Chargeback exports **clean-Gbps usage**; converting Gbps → money is the finance system's job, not the gateway's |
| Proration for mid-period create/delete | p95 tolerates sparse samples; no proration in v1 (D-CHG-3) |
| Alert firing on overage / SLA breach | M6 *Alerting*; billing may surface overage but fires nothing |

---

## User Stories

### P1: Billing sample series (worker) ⭐ MVP

**User Story**: As the chargeback pipeline, I want a dedicated coarse per-service clean-bps sample series
so that p95 can be computed over a full billing period independent of the short-lived telemetry windows.

**Why P1**: p95-over-a-month cannot read the ≤2 s `TelemetryCounter` rows (they are pruned on a short
horizon and would be ~1.3 M rows/service/month). A decoupled 5-minute series is the industry standard for
95th-percentile ("burstable") bandwidth billing and is the source everything else here computes from. (D-CHG-1)

**Acceptance Criteria**:

1. WHEN the worker is running THEN it SHALL append a per-service `BillingSample(service_id, sample_ts, clean_bps)` at a configurable billing cadence (**default 300 s**) via an in-worker background task (same lane pattern as telemetry aggregation, A-CHG-2), reading the exact per-service `clean_bytes` counter (D-TEL-1 source, A-CHG-1).
2. WHEN a sample is taken THEN `clean_bps` SHALL be the exact clean-byte **delta** since the previous sample ÷ elapsed seconds — billing-grade, decoupled from ringbuf/perf sampling (AD-006).
3. WHEN a clean-byte delta is negative (BPF reload/reset detected) THEN the sampler SHALL treat it as a reset and record the raw post-reset value rather than a negative sample (mirrors TEL-09); reset events SHALL be observable.
4. WHEN a sample already exists for `(service_id, sample_ts)` THEN re-running SHALL be idempotent (no duplicate row, no double count).
5. WHEN a service has no clean traffic in an interval THEN the sampler SHALL record a `0`-bps sample — sample **presence** anchors the p95 denominator; a missing interval (worker down) is not the same as a zero.
6. WHEN a service's `dp_id` is stale (deleted/unmapped service) THEN it SHALL be reconciled/ignored (no sample written against a non-existent service), same reconciliation posture as telemetry (A-TEL-6 / A-CHG-5).
7. WHEN Postgres or the data-plane reader is unavailable THEN the sampler SHALL degrade bounded (skip the interval, log, resume next tick) without crashing the worker.
8. WHEN samples age past retention (owning period finalized + a grace horizon) THEN old `BillingSample` rows SHALL be pruned; the table SHALL stay bounded (~8,640 rows/service/month at 300 s).

**Independent Test**: Run the worker (or its sampler) against a seeded/faked clean-byte reader; advance time; assert `BillingSample` rows appear at the cadence with correct `clean_bps` deltas; feed a counter reset and assert no negative sample; kill DB mid-run and assert the worker survives and resumes.

---

### P1: p95 metering & `BillingUsage` rollup (worker) ⭐ MVP

**User Story**: As internal finance, I want an immutable per-service per-period `BillingUsage` record so
that I can charge back committed/actual clean bandwidth.

**Why P1**: This is the feature's deliverable — the settled number chargeback exports. Everything else feeds it.

**Acceptance Criteria**:

1. WHEN a billing period closes (a UTC **calendar-month** boundary passes, D-CHG-3) THEN an in-worker period-close job SHALL compute, per service active in the period, `p95_clean_gbps` = 95th percentile of that period's `BillingSample.clean_bps`, converted to Gbps.
2. WHEN p95 is computed THEN the job SHALL write `billed_gbps = max(committed_clean_gbps, p95_clean_gbps)` and `overage_gbps = max(0, p95_clean_gbps − committed_clean_gbps)` (TDD §4.8).
3. WHEN a `BillingUsage` row is written THEN it SHALL **snapshot** `billing_metric`, `overage_policy`, and `committed_clean_gbps` effective at period close, so the historical record stays self-contained if the plan later changes (A-CHG-4).
4. WHEN `overage_policy = capped` THEN the record SHALL still store `billed_gbps = max(committed, p95)` and `overage_gbps` — enforcement already bounds p95 ≤ ceiling, so the policy governs finance interpretation, not the formula (TDD §4.8); WHEN `overage_policy = billed` THEN `overage_gbps` is chargeable overage.
5. WHEN a period is finalized THEN its `BillingUsage` row SHALL be immutable (`status = final`) and unique per `(service_id, period_start)`; re-running the close job SHALL NOT duplicate or mutate a finalized row (idempotent, A-CHG-idempotent).
6. WHEN the current period is in progress THEN the job SHALL maintain a running current-period `BillingUsage` estimate (`status = open`, refreshed each cadence) for showback, kept distinct from finalized rows.
7. WHEN a service has zero samples / zero clean traffic for the period THEN `p95_clean_gbps = 0` and `billed_gbps = committed_clean_gbps` (committed is the floor); `sample_count` SHALL be recorded so low-confidence periods are visible.
8. WHEN a service is deleted mid-period THEN its open period SHALL be finalized up to deletion and the `BillingUsage` row SHALL survive the deletion (FK `ON DELETE SET NULL` + denormalized `tenant_id` and service-name snapshot for attribution).
9. WHEN per-service billed bytes are compared to node-level clean bytes THEN they SHALL reconcile within counter-reset tolerance — the billing byte source is the exact per-CPU counter, independent of sampling (PROJECT constraint).
10. WHEN Postgres is unavailable at period close THEN the job SHALL retry on a later tick without corrupting prior periods or double-writing (bounded degrade).

**Independent Test**: Seed a known `BillingSample` series for a service with `committed_clean_gbps = C`; run the period-close over a closed month; assert `p95_clean_gbps` equals the computed 95th percentile, `billed_gbps = max(C, p95)`, `overage_gbps = max(0, p95−C)`, `status = final`; re-run and assert exactly one immutable row (idempotent).

---

### P1: Billing usage API (control-plane) ⭐ MVP

**User Story**: As a tenant/admin, I want `GET /billing/usage` so that I can see billed clean-Gbps for my
services (tenant) or the whole node (admin).

**Why P1**: The read surface finance and tenants consume; the isolation boundary that keeps tenants apart.

**Acceptance Criteria**:

1. WHEN a `tenant_user` GETs `/billing/usage` (optionally `?service_id=…&period=…`) THEN the API SHALL return **their own** services' usage (running current + finalized periods): `p95_clean_gbps, committed_clean_gbps, billed_gbps, overage_gbps, overage_policy, billing_metric, period_start, period_end, status, sample_count`.
2. WHEN a `tenant_user` requests billing for a service they do not own THEN the API SHALL fail-closed (404) — the same `load_service_for_principal` tenant-ownership guard as telemetry/services (5.2, A-CHG-6).
3. WHEN an `admin` GETs `/billing/usage` THEN the API SHALL return node-wide usage across all tenants/services, filterable by `tenant_id` / `service_id` / `period`.
4. WHEN a running (open) period is returned THEN it SHALL be marked `status = open` (provisional estimate) versus `final` — clients render provisional vs settled.
5. WHEN no usage exists yet (fresh service, or no period has closed) THEN the API SHALL return an explicit empty/zeroed payload with a "no billing data yet" marker, not an error.
6. WHEN any billing endpoint is called THEN it SHALL be read-only, create no mutation, and pass through the existing session/RBAC middleware (A-CHG-6).
7. WHEN Gbps values are serialized THEN they SHALL use the same `Numeric(10,2)` Decimal convention as `ServicePlan`, so committed / billed / p95 are directly comparable (A-CHG-4).

**Independent Test**: Seed `BillingUsage` rows; assert the owning tenant gets its service's usage, a non-owner gets 404, and admin gets the node-wide list; assert an open period is labeled provisional and an empty service returns 200 with the no-data marker.

---

### P2: Export for chargeback

**User Story**: As an admin, I want to export finalized usage so that the internal chargeback/finance
system can ingest it.

**Why P2**: Required to actually charge back, but the core metering + read surface prove the slice first.

**Acceptance Criteria**:

1. WHEN an admin requests `GET /billing/usage/export?period=…&format=csv|json` THEN the API SHALL return **finalized** `BillingUsage` rows for the period in the requested format.
2. WHEN an export row is generated THEN it SHALL include service, tenant, period bounds, `committed_clean_gbps`, `p95_clean_gbps`, `billed_gbps`, `overage_gbps`, `overage_policy`, and `sample_count`.
3. WHEN a period is not yet finalized THEN export SHALL omit it or clearly mark it provisional — an open period SHALL NOT be exported as settled.

**Independent Test**: Finalize a month for two services across two tenants; export CSV and JSON; assert row counts/values match the stored finalized rows and no open period is included as settled.

---

### P2: Billing showback in the SPA

**User Story**: As a tenant/admin, I want to see billed-vs-committed clean-Gbps in the dashboard so that I
understand my chargeback exposure without reading the API.

**Why P2**: Additive UI on top of the API; reuses the telemetry SPA shell (D-TEL-2) and is gated on it.

**Acceptance Criteria**:

1. WHEN a tenant opens the SPA billing view THEN it SHALL show, per service, current running billed-Gbps vs `committed_clean_gbps` and a list of finalized periods, reusing the telemetry SPA shell/auth/polling.
2. WHEN an admin opens the SPA billing view THEN it SHALL show node/tenant-wide billed vs committed with `overage_gbps` visually flagged.
3. WHEN a period is open/provisional THEN the UI SHALL label it provisional and never present it as a settled invoice.

**Independent Test**: With seeded usage, log in as tenant and see own service billed-vs-committed with a provisional current period; log in as admin and see the node-wide list with overage flagged; confirm a tenant cannot see another tenant's usage.

---

### P3: Billing history & trend

**User Story**: As an admin/tenant, I want a short history/trend of billed and p95 Gbps across periods so
that I can see usage over time.

**Why P3**: Nice-to-have; the latest/running period satisfies the MVP.

**Acceptance Criteria**:

1. WHEN a billing view is open THEN it SHALL render a trend of `billed_gbps` / `p95_clean_gbps` across the retained finalized periods.
2. WHEN a user requests it THEN the API SHALL return a service's / tenant's finalized-period history.

**Independent Test**: Finalize several months; assert the history endpoint returns them in order and the chart plots billed/p95 per period.

---

## Edge Cases

- WHEN the BPF program reloads mid-period (clean-byte counter resets) THEN the sampler SHALL detect the reset and record the raw post-reset value; p95 SHALL be computed over the available samples without a negative spike (CHG-03).
- WHEN a service is created mid-period THEN it SHALL have fewer samples; p95 is computed over what exists with no proration; `sample_count` reflects the partial period (D-CHG-3, CHG-15).
- WHEN a service is deleted mid-period THEN its usage SHALL be finalized up to deletion and survive service deletion via `SET NULL` + denormalized attribution (CHG-16).
- WHEN a service has zero clean traffic for the whole period THEN `p95 = 0` and `billed_gbps = committed` (committed floor), `sample_count` recorded (CHG-15).
- WHEN `committed_clean_gbps` changes mid-period THEN the finalized record SHALL snapshot the value effective at period close (documented; no intra-period time-weighting in v1) (CHG-11).
- WHEN the worker is down for part of a period THEN sample gaps reduce confidence but SHALL NOT produce negative or fabricated samples; `sample_count` exposes the gap (CHG-05/07).
- WHEN the gateway BPF program is not loaded THEN no per-service clean bytes exist; samples are absent and any finalized period reports `p95 = 0` / `billed = committed` with `sample_count = 0` (CHG-15).
- WHEN the period-close job is re-run (crash/restart) for an already-finalized period THEN it SHALL be idempotent — one immutable row per `(service_id, period_start)` (CHG-13).
- WHEN a config-map double-buffer swap occurs THEN clean-byte counters (unslotted runtime state) SHALL be unaffected; sampling stays consistent (A-CHG-5).

---

## Requirement Traceability

| Requirement ID | Story | Phase | Status |
| --- | --- | --- | --- |
| CHG-01 | P1: Billing sample series | - | Pending |
| CHG-02 | P1: Billing sample series | - | Pending |
| CHG-03 | P1: Billing sample series | - | Pending |
| CHG-04 | P1: Billing sample series | - | Pending |
| CHG-05 | P1: Billing sample series | - | Pending |
| CHG-06 | P1: Billing sample series | - | Pending |
| CHG-07 | P1: Billing sample series | - | Pending |
| CHG-08 | P1: Billing sample series | - | Pending |
| CHG-09 | P1: p95 metering & rollup | - | Pending |
| CHG-10 | P1: p95 metering & rollup | - | Pending |
| CHG-11 | P1: p95 metering & rollup | - | Pending |
| CHG-12 | P1: p95 metering & rollup | - | Pending |
| CHG-13 | P1: p95 metering & rollup | - | Pending |
| CHG-14 | P1: p95 metering & rollup | - | Pending |
| CHG-15 | P1: p95 metering & rollup | - | Pending |
| CHG-16 | P1: p95 metering & rollup | - | Pending |
| CHG-17 | P1: p95 metering & rollup | - | Pending |
| CHG-18 | P1: p95 metering & rollup | - | Pending |
| CHG-19 | P1: Billing usage API | - | Pending |
| CHG-20 | P1: Billing usage API | - | Pending |
| CHG-21 | P1: Billing usage API | - | Pending |
| CHG-22 | P1: Billing usage API | - | Pending |
| CHG-23 | P1: Billing usage API | - | Pending |
| CHG-24 | P1: Billing usage API | - | Pending |
| CHG-25 | P1: Billing usage API | - | Pending |
| CHG-26 | P2: Export for chargeback | - | Pending |
| CHG-27 | P2: Export for chargeback | - | Pending |
| CHG-28 | P2: Export for chargeback | - | Pending |
| CHG-29 | P2: Billing showback SPA | - | Pending |
| CHG-30 | P2: Billing showback SPA | - | Pending |
| CHG-31 | P2: Billing showback SPA | - | Pending |
| CHG-32 | P3: Billing history & trend | - | Pending |
| CHG-33 | P3: Billing history & trend | - | Pending |

**ID format:** `CHG-[NUMBER]`

**Status values:** Pending → In Design → In Tasks → Implementing → Verified

**Coverage:** 33 total, 0 mapped to tasks (Tasks phase pending). P1 = CHG-01..25 (MVP vertical slice:
sample series → p95 rollup → API); P2 = CHG-26..31 (export + SPA showback); P3 = CHG-32..33 (history/trend).

---

## Success Criteria

How we know the feature is successful:

- [ ] For a known `BillingSample` series and `committed_clean_gbps = C`, the finalized `BillingUsage` shows `p95_clean_gbps` = the 95th percentile and `billed_gbps = max(C, p95)`, `overage_gbps = max(0, p95−C)`.
- [ ] Billed clean bytes reconcile to the exact per-CPU clean-byte counter, independent of ringbuf/perf sampling (billing-grade, AD-006).
- [ ] Period-close produces immutable monthly `BillingUsage` rows; re-running is idempotent (one row per service/period).
- [ ] Tenant isolation holds: a tenant cannot read another tenant's `BillingUsage` via API or SPA (zero leakage in the test set).
- [ ] Export returns finalized rows matching stored usage; no open period is exported as settled.
- [ ] The control-plane test-suite baseline stays green with the new billing sampler, rollup, and API wired.
