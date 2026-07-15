# SLA/OLA Reporting & Audit Specification

**Feature:** M6 #3 — SLA/OLA reporting & audit (final M6 feature)
**Context:** `.specs/features/sla-ola-reporting/context.md` (D-SLA-1..4, A-SLA-1..8)
**Status:** **Draft** (2026-07-14, awaiting approval) → Design
**Depends on:**
- **Chargeback metering executed** (M5, **satisfied** — `BillingUsage` model + migration `_0009` + `/billing`
  endpoints + metering lane all landed in-tree). Provides the immutable per-period `BillingUsage` the SLA
  report ties to (`committed`/`p95`/`billed`/`overage`, `status` open→final, UTC calendar-month periods via
  `billing_period.py`). The billing dimension is **buildable now** (source present).
- **agent-worker executed** (M4 #1, **satisfied**) — the long-running worker + background-lane pattern
  (`billing`/`telemetry`/`alert`/`node_control_reconciler`) the report-materialization lane mirrors; no new
  Redis `JobType`.
- **Reuses (executed):** M1 auth/RBAC (`require_admin`, `load_service_for_principal`, fail-closed tenant
  ownership, `Principal.tenant_id`) + `AuditEvent` + `record_event`/`scrub_metadata` (the **write path is
  already complete** — 41 call sites, full service/rule/list/feed/user + dangerous-action taxonomy, **no
  audit read router exists yet**); apply-status `AgentJob` (apply-failed evidence); M4 #3 `FeedSyncRun`
  (feed-health evidence); billing/telemetry export + retention/prune patterns; M5 SPA shell (P2 panel).
- **Soft coordination (NOT hard gates):** Alerting (M6 #2) persists the fairness-breach `Alert` instances used
  as the **committed-clean-bandwidth-honored** evidence source — its models/migration `_0011` have landed and
  the evaluator lane is being executed now; until fairness-breach alerts are present that one SLA dimension
  degrades to `insufficient_data` (A-SLA-5, fail-safe — never fabricated). Bypass-maintenance (M6 #1) supplies
  `NodeControl` (migration `_0010`, present) + `node.bypass.*`/`node.maintenance.*` audit events for the OLA
  bypass/maintenance-window dimension; absent windows → that dimension reports zero, no error.

---

## Problem Statement

The gateway makes explicit per-tenant commitments — above all **committed clean bandwidth honored even when
a neighbour is flooded** (the one hard SLA per PROJECT/AD-007) — plus a set of internal operational
commitments (config propagation, feed health, maintenance/bypass conduct) that live in the OLA. Every one of
these is now *measured or recorded* somewhere (fairness-breach alerts, `BillingUsage`, `AgentJob`,
`FeedSyncRun`, `NodeControl`, the audit log), but **no artifact tells a tenant whether their SLA was met last
month**, and **no one can query the audit trail** even though the write path records every service/rule/list/
feed/user change and every dangerous admin action. This feature adds the M6 **reporting & audit** surface: a
**worker-side lane** that materializes an **immutable per-tenant, per-period SLA report** (met/missed per
dimension, tied to the `BillingUsage` period) plus an **admin OLA operational summary**, and an **admin-only
queryable/exportable view over the existing audit log** — all **control-plane/worker only, reading only
already-persisted evidence, zero hot-path change, no new data-plane surface, and no fabricated numbers**
(commitments with no Pilot measurement source — Availability, added-latency p99 — are disclosed as
**best-effort / not measured**, not scored).

## Goals

- [ ] A worker-side lane **materializes** a per-(tenant, period) SLA report from **already-persisted evidence
      only** — a running **`open`** estimate for the current period finalized to an **immutable `final`** row
      at period close (mirrors the `BillingUsage` open→final lifecycle and the executed billing/telemetry/
      alert worker lanes) — **no hot-path change, no new counters, no fabricated values**.
- [ ] Each report scores a **fixed catalog of dimensions** as **met / missed / best_effort / insufficient_data**
      against a target, with a **link to the persisted evidence** (fairness-breach `Alert` durations,
      `BillingUsage`, `AgentJob failed`, `FeedSyncRun`, bypass/maintenance windows) — and **explicitly
      discloses** Availability and added-latency-p99 as **best-effort, not measured at Pilot** (AD-007,
      A-TEL-8) rather than pass/fail.
- [ ] Reports are **queryable** with strict **tenant isolation**: a tenant sees only their own services'
      **SLA** dimensions (§5.2); an admin sees every tenant's SLA report **and** the node-scoped **OLA**
      operational summary (apply reliability, feed health, bypass/maintenance windows).
- [ ] The existing **audit log is queryable and exportable** by an admin — filter by actor / action /
      target / outcome / time — with **secrets already scrubbed at write** (`scrub_metadata`) and **no
      write-path change** (the taxonomy already covers service/rule/list/feed/user + dangerous actions).
- [ ] Reports and audit history are **exportable** (CSV/JSON) and subject to a **bounded retention prune**,
      consistent with the executed telemetry/billing retention pattern; the whole feature is
      **restart-safe** (materialization is idempotent per (tenant, period)).

## Out of Scope

| Feature | Reason |
| --- | --- |
| **Availability** SLA scoring / uptime % | Single-node fail-closed = SPOF → Availability is **best-effort, NOT under SLA** at Pilot (AD-007). Reported as a disclosed best-effort dimension, never scored met/missed. HA/Availability SLA is a GA blocker (CM-01, M7). |
| **Added-latency p99 ≤ 1 ms** scoring | No in-band latency measurement path exists in v1 (A-TEL-8). Disclosed as `best_effort`/not-measured; a real measurement source is a later feature. |
| **Clean-accuracy** (zero false drop) per-tenant scoring | Only measurable against the v1 test set, not per-tenant in production. Not a per-period report dimension. |
| New **audit write** coverage / new `record_event` sites | The write path + dangerous-action taxonomy are already complete; this feature adds only the **read/query/export** surface (D-SLA-3). |
| A **new exact per-period "committed-honored" DP counter** | Committed-honored met/missed is derived from persisted **fairness-breach `Alert`** evidence (A-SLA-5); no new data-plane/telemetry counter — preserves the recent M6 "zero DP change" posture. |
| **Per-tenant self-service audit** trail | `AuditEvent` is actor-oriented with no `tenant_id`; tenant-scoped audit read is deferred (D-SLA-3). Tenants get the SLA **report**, admins get the audit **log**. |
| **Scheduled email delivery** of reports | v1 is API + export + SPA (D-SLA-4). Emailing the finalized report (reusing Alerting's SMTP channel) is a P3 forward hook only, not built. |
| **Real-time / streaming** SLA | Reports are periodic (per `BillingUsage` period) + a running open estimate refreshed on the lane tick; not a live dashboard (that is M5 telemetry). |
| **Custom / tenant-defined** SLA targets or report dimensions | v1 is a fixed catalog with admin-tunable targets; an open report builder is out of scope. |

---

## User Stories

### P1: SLA/OLA report materialization lifecycle ⭐ MVP

**User Story:** As the **operations platform**, I want a worker lane that builds a per-tenant, per-period SLA
report (and an admin OLA summary) from already-persisted evidence — a running estimate for the open period,
frozen immutably at period close — so the met/missed record is durable, idempotent, and restart-safe.

**Why P1:** This is the engine; every other story (dimension scoring, query, export, dashboard) is inert
without a materialized, immutable-at-close report row.

**Acceptance Criteria:**

1. WHEN the report-materialization background lane runs a tick THEN it SHALL compute each report **entirely
   off the hot path** (worker/control-plane only, reading only already-persisted evidence — no DP call, no
   new counter). `(SLA-01)`
2. WHEN a reporting **period is still open** (current UTC calendar month, aligned to the `BillingUsage`
   period) THEN the lane SHALL upsert a **running `open`** report per (tenant, period) reflecting
   evidence-to-date. `(SLA-02)`
3. WHEN a reporting **period closes** THEN the lane SHALL finalize each (tenant, period) report to an
   **immutable `final`** row (record `finalized_at`), computed once and never recomputed on read. `(SLA-03)`
4. WHEN materialization runs repeatedly for the same (tenant, period) THEN it SHALL be **idempotent** — a
   unique `(tenant, period_start)` report, updated in place while `open`, never duplicated. `(SLA-04)`
5. WHEN a service (and thus a tenant) is created or **deleted mid-period** THEN the report SHALL still close
   correctly — evidence coverage is reflected (e.g. a `coverage`/sample indicator), and a deleted service's
   period rows survive via denormalized `tenant_id`/`service_name` snapshots (SET NULL, billing precedent).
   `(SLA-05)`
6. WHEN the worker/node **restarts** THEN in-progress materialization SHALL resume with no duplicate `final`
   rows and no lost open-period state (idempotent by (tenant, period)). `(SLA-06)`
7. WHEN an evidence source is **absent or errors** for a tick (e.g. a not-yet-executed feature, or a query
   failure) THEN the lane SHALL mark the affected **dimension** `insufficient_data` and continue — never
   crash the lane, never block other tenants/dimensions (fail-safe, bounded). `(SLA-07)`
8. WHEN the lane runs THEN it SHALL **not** be a Redis `JobType`/`AgentJob` — it is a periodic worker
   background lane (like billing/telemetry/alert), catch-log-continue on error. `(SLA-08)`

**Independent Test:** Drive the lane with synthetic evidence rows for two tenants: assert an `open` report
appears for the current period and updates in place across ticks; roll the clock past the period boundary and
assert exactly one immutable `final` row per (tenant, period) with `finalized_at`; re-run the lane and assert
no duplicate/no mutation of `final`; remove an evidence source and assert only that dimension flips to
`insufficient_data`; restart mid-materialization and assert no duplicate `final`.

---

### P1: SLA/OLA dimension catalog & met/missed scoring ⭐ MVP

**User Story:** As a **tenant / system admin**, I want each report to score a fixed, well-defined set of
dimensions as met/missed against a target with a pointer to the evidence, so the report is trustworthy and
never invents a number it cannot measure.

**Why P1:** A materialized report with no scored dimensions is empty; this story binds each dimension to its
persisted evidence source, target, and status semantics.

**Acceptance Criteria:** (`met` = target honored; `missed` = target breached; `best_effort` = disclosed,
not scored; `insufficient_data` = evidence absent for the period — A-SLA-5)

1. WHEN scoring the **committed-clean-bandwidth-honored** SLA dimension (per service → owning tenant) THEN
   the system SHALL derive met/missed from persisted **fairness-breach `Alert`** instances (Alerting M6 #2)
   over the period — e.g. breach-seconds / breach-count / honored-fraction against a tunable target — and
   mark it `insufficient_data` if the fairness-breach source is unavailable (Alerting not executed). `(SLA-09)`
2. WHEN scoring the **billing / chargeback** SLA dimension THEN the system SHALL read the finalized
   `BillingUsage` for the period (`billed_gbps = max(committed, p95)`, `overage_gbps`, `overage_policy`) and
   surface it as the report's chargeback tie-in — the report **never recomputes** p95 (reads the immutable
   billing row). `(SLA-10)`
3. WHEN scoring the **config-propagation reliability** OLA dimension (node/admin) THEN the system SHALL count
   `AgentJob` reaching **`failed`** in the period against a tunable target (e.g. 0 apply failures = met).
   `(SLA-11)`
4. WHEN scoring the **feed-sync health** OLA dimension (node/admin) THEN the system SHALL summarize
   `FeedSyncRun` failures per source over the period against a tunable target. `(SLA-12)`
5. WHEN scoring the **bypass / maintenance conduct** OLA dimension (node/admin) THEN the system SHALL derive
   total **bypass** and **maintenance** windows from `node.bypass.*`/`node.maintenance.*` `AuditEvent`s +
   `NodeControl` state, disclosing count and total duration (traffic-through-bypass is chargeback-excluded,
   A-CHG-8) — reporting zero windows (not an error) if bypass-maintenance is not executed. `(SLA-13)`
6. WHEN a commitment has **no Pilot measurement source** — **Availability** (AD-007) and **added-latency
   p99** (A-TEL-8) — THEN the report SHALL include it as a **`best_effort`** dimension with an explicit
   "not measured at Pilot" disclosure and **SHALL NOT** score it met/missed or fabricate a value. `(SLA-14)`
7. WHEN a dimension is scored THEN the report SHALL record its **target**, **measured value**, **status**
   (`met`/`missed`/`best_effort`/`insufficient_data`), and a **link/reference to the persisted evidence**
   (e.g. the alert ids, billing period, job/feed-run counts) used. `(SLA-15)`
8. WHEN an admin **overrides** a dimension's target (e.g. the committed-honored tolerance, allowed apply
   failures) THEN the system SHALL persist it and use it for subsequent scoring; an un-overridden dimension
   SHALL use its **documented default target**; reading the catalog SHALL show effective values. `(SLA-16)`
9. WHEN a report is scored THEN **SLA** (tenant-facing) and **OLA** (internal-operational) dimensions SHALL
   be **distinctly classified** so tenant reads expose only SLA dimensions and admin reads expose both.
   `(SLA-17)`

**Independent Test:** For each dimension, inject its evidence (a firing→resolved fairness-breach alert of
known duration; a finalized `BillingUsage`; N `failed` jobs; a failing `FeedSyncRun`; a bypass window) and
assert the correct status + measured value + evidence reference; assert Availability/p99 appear as
`best_effort` with no score; remove the fairness-breach source and assert `insufficient_data`; override a
target and assert the status flips accordingly.

---

### P1: SLA/OLA report query API & tenant isolation ⭐ MVP

**User Story:** As a **tenant**, I want to read my own per-period SLA report, and as an **admin** I want to
read every tenant's SLA report plus the node OLA summary, so the commitments are visible and auditable
without leaking across tenants.

**Why P1:** A materialized report nobody can read is inert; isolation is the security-critical half (§5.2).

**Acceptance Criteria:**

1. WHEN a **tenant** GETs SLA reports THEN the system SHALL return **only** reports for services/periods they
   own (filtered by denormalized `tenant_id` + `load_service_for_principal`→404 when a service is named) —
   **never** another tenant's report and **never** node-scoped OLA dimensions (§5.2). `(SLA-18)`
2. WHEN an **admin** GETs SLA reports THEN the system SHALL return **all** tenants' reports **and** the
   node-scoped **OLA** operational summary, filterable by tenant / service / period / status. `(SLA-19)`
3. WHEN a report is read THEN the API SHALL return the **stored** row (open or final) with its per-dimension
   status/target/measured-value/evidence — it SHALL **never recompute** scoring on read (materialization is
   the only writer). `(SLA-20)`
4. WHEN a non-admin requests node/OLA or another tenant's report THEN the system SHALL **fail closed** (403/
   404, no partial leak). `(SLA-21)`
5. WHEN reports are listed THEN the API SHALL support filtering by **period** (`YYYY-MM`) and **status**
   (`open`/`final`) and default to the most recent finalized period. `(SLA-22)`

**Independent Test:** Materialize reports for tenant A and tenant B; GET as A and assert only A's SLA
dimensions (no OLA, no B); GET as admin and assert both tenants + the OLA summary; request B's report as A and
assert 404; request the node OLA as a tenant and assert 403; filter by `period` and `status` and assert the
correct subset.

---

### P1: Audit log query surface (admin) ⭐ MVP

**User Story:** As a **system admin / auditor**, I want to query the audit log — who did what, to which
resource, when, with what outcome — so dangerous actions and configuration changes are reviewable after the
fact.

**Why P1:** The write path already records everything; without a read surface the audit log is
write-only and operationally useless.

**Acceptance Criteria:**

1. WHEN an admin GETs the audit log THEN the system SHALL return `AuditEvent` rows (actor, action,
   target_type/id, outcome, ip, scrubbed metadata, `created_at`) **paginated** and **newest-first**
   (`ix_audit_events_created_at`). `(SLA-23)`
2. WHEN an admin filters the audit log THEN the system SHALL support filtering by **actor**, **action**
   (incl. dangerous-action classes: `tenant.delete`, `service.delete`/`service.enable`, `feed.delete`,
   `node.bypass.*`, `node.maintenance.*`), **target_type/target_id**, **outcome**, and **time range**.
   `(SLA-24)`
3. WHEN audit metadata is returned THEN **secrets SHALL already be absent** (scrubbed at write by
   `scrub_metadata`) — the read path SHALL NOT re-expose any secret/credential/PII. `(SLA-25)`
4. WHEN a **non-admin** requests the audit log THEN the system SHALL **fail closed** (403, no leak) — audit
   read is **admin-only** (`AuditEvent` has no `tenant_id`; tenant self-audit is out of scope, D-SLA-3).
   `(SLA-26)`
5. WHEN the audit log is queried THEN the read path SHALL be **read-only** — no new `AuditEvent` write, no
   change to the existing write taxonomy. `(SLA-27)`

**Independent Test:** As admin, GET the audit log and assert newest-first pagination; filter by
`action=tenant.delete` and by a time range and assert the correct subset; assert a scrubbed-secret metadata
row returns no secret value; as a non-admin assert 403; assert no `AuditEvent` is written by a read.

---

### P2: SLA/OLA dashboard surface *(gated on the M5 telemetry SPA shell executed)*

**User Story:** As a **dashboard user**, I want to see my SLA report (or, as admin, all reports + the OLA
summary + a recent-audit view) in the SPA so I can review commitments without leaving the console.

**Why P2:** The state + API exist via P1; the visual surface layers on the executed M5 SPA shell.

**Acceptance Criteria:**

1. WHEN a tenant opens the dashboard THEN the SPA SHALL show their **latest finalized SLA report** (per
   dimension: status, target, measured value) plus a period selector, scoped to their own services. `(SLA-28)`
2. WHEN an admin opens the dashboard THEN the SPA SHALL additionally show the **node OLA summary** and a
   **recent-audit** panel (newest-first, filterable), reusing the telemetry poll/RBAC patterns. `(SLA-29)`
3. WHEN a report dimension is shown THEN it SHALL be status-colored (met/missed/best_effort/insufficient_data)
   consistent with the §9.1 `theme/thresholds.ts` display convention. `(SLA-30)`

**Independent Test:** With the SPA running, materialize a report with a mix of met/missed/best_effort
dimensions; assert the tenant view shows only their SLA dimensions colored by status and the admin view adds
the OLA summary + recent-audit panel; assert a tenant never sees OLA or another tenant's report.

---

### P2: Report & audit export

**User Story:** As a **system admin (and tenant, for their own SLA report)**, I want to export reports and
the audit log so I can archive them and share them for compliance/chargeback review.

**Why P2:** Export refines P1's read APIs; useful for compliance but not required for the reports to exist.

**Acceptance Criteria:**

1. WHEN an admin exports SLA reports THEN the system SHALL emit **CSV/JSON** of finalized reports (all
   tenants) with a stable column contract; a **tenant** export SHALL contain only their own reports. `(SLA-31)`
2. WHEN an admin exports the **audit log** THEN the system SHALL emit **CSV/JSON** honoring the same
   filters (actor/action/target/outcome/time), admin-only, with secrets absent. `(SLA-32)`
3. WHEN an export is produced THEN it SHALL reuse the executed billing/telemetry export pattern (finalized/
   filtered rows, bounded) and require the same RBAC as the corresponding read. `(SLA-33)`

**Independent Test:** Export SLA reports as admin (all tenants) vs a tenant (own only) and assert the row
scope + column contract; export the audit log filtered by `action` + time range and assert the subset with no
secret columns; assert a non-admin cannot export the audit log or node/OLA rows.

---

### P3: Retention/prune, report regeneration & scheduled-delivery hook

**User Story:** As a **system admin / SRE**, I want bounded retention on report/audit history, a way to
regenerate/annotate a period, and a forward hook for emailing reports, so the surface stays clean and
correctable over time.

**Why P3:** Operational ergonomics — the reporting/audit surface works without them.

**Acceptance Criteria:**

1. WHEN retention runs THEN old report rows and (per policy) audit rows beyond a **bounded retention horizon**
   SHALL be pruned, consistent with the telemetry/billing/alert retention pattern; `final` reports within the
   horizon SHALL be preserved. `(SLA-34)`
2. WHEN an admin **regenerates** a still-correctable period, or **annotates** a report (e.g. a credit note /
   incident reference), THEN the system SHALL record the change and **audit** it — a `final` report's scored
   values SHALL otherwise remain immutable. `(SLA-35)`
3. WHEN a **scheduled email delivery** of the finalized report is later enabled THEN it SHALL be able to
   reuse Alerting's SMTP channel via a documented hook — v1 ships the hook/seam only, **not** automated
   sending (D-SLA-4). `(SLA-36)`

**Independent Test:** Run the prune and assert rows past the horizon are removed while recent `final` reports
remain; annotate a report and assert the annotation persists + is audited while scored values are unchanged;
assert the delivery hook exists and is inert (no email sent) in v1.

---

## Edge Cases

- WHEN a fairness-breach `Alert` **spans a period boundary** THEN each period's committed-honored dimension
  SHALL count only the breach-seconds falling within that period (no double-count). `(→ SLA-09, SLA-03)`
- WHEN a tenant has **no services** in a period THEN their SLA report SHALL be **empty/absent** rather than a
  fabricated all-met report (coverage indicator, A-SLA-5). `(→ SLA-05, SLA-14)`
- WHEN the **billing** dimension's `BillingUsage` is still `open` (period not closed) THEN the SLA report's
  billing tie-in SHALL read the running estimate and mark itself accordingly — finalize only after billing
  finalizes. `(→ SLA-10, SLA-03)`
- WHEN Alerting is **not yet executed** THEN the committed-honored dimension SHALL be `insufficient_data`, the
  rest of the report SHALL still materialize, and no error SHALL surface. `(→ SLA-07, SLA-09)`
- WHEN bypass-maintenance is **not executed** THEN the bypass/maintenance OLA dimension SHALL report **zero
  windows** (a valid measured value), not an error. `(→ SLA-13)`
- WHEN a service is **deleted mid-period** THEN its finalized report row SHALL survive via the denormalized
  `tenant_id`/`service_name` snapshot (SET NULL), consistent with `BillingUsage`. `(→ SLA-05)`
- WHEN a tenant requests a **node/OLA** dimension or **another tenant's** report/audit THEN the system SHALL
  fail closed with no partial data (§5.2). `(→ SLA-18, SLA-21, SLA-26)`
- WHEN audit metadata **would** contain a secret (e.g. an SMTP/webhook credential from Alerting config, a feed
  credential) THEN it SHALL already be scrubbed at write and never re-exposed on read/export. `(→ SLA-25, SLA-32)`
- WHEN a report is read while the lane is **mid-materialization** THEN the read SHALL return a consistent
  row (last committed `open`/`final`), never a partially-written report. `(→ SLA-20, SLA-04)`
- WHEN an **Availability** or **p99-latency** value is requested THEN the surface SHALL present the
  `best_effort` disclosure text and **no numeric pass/fail** — never an implied uptime figure. `(→ SLA-14)`

---

## Requirement Traceability

| Requirement ID | Story | Phase | Status |
| --- | --- | --- | --- |
| SLA-01 | P1: Report materialization lifecycle | Design | Pending |
| SLA-02 | P1: Report materialization lifecycle | Design | Pending |
| SLA-03 | P1: Report materialization lifecycle | Design | Pending |
| SLA-04 | P1: Report materialization lifecycle | Design | Pending |
| SLA-05 | P1: Report materialization lifecycle | Design | Pending |
| SLA-06 | P1: Report materialization lifecycle | Design | Pending |
| SLA-07 | P1: Report materialization lifecycle | Design | Pending |
| SLA-08 | P1: Report materialization lifecycle | Design | Pending |
| SLA-09 | P1: Dimension catalog & scoring | Design | Pending |
| SLA-10 | P1: Dimension catalog & scoring | Design | Pending |
| SLA-11 | P1: Dimension catalog & scoring | Design | Pending |
| SLA-12 | P1: Dimension catalog & scoring | Design | Pending |
| SLA-13 | P1: Dimension catalog & scoring | Design | Pending |
| SLA-14 | P1: Dimension catalog & scoring | Design | Pending |
| SLA-15 | P1: Dimension catalog & scoring | Design | Pending |
| SLA-16 | P1: Dimension catalog & scoring | Design | Pending |
| SLA-17 | P1: Dimension catalog & scoring | Design | Pending |
| SLA-18 | P1: Report query API & isolation | Design | Pending |
| SLA-19 | P1: Report query API & isolation | Design | Pending |
| SLA-20 | P1: Report query API & isolation | Design | Pending |
| SLA-21 | P1: Report query API & isolation | Design | Pending |
| SLA-22 | P1: Report query API & isolation | Design | Pending |
| SLA-23 | P1: Audit log query surface | Design | Pending |
| SLA-24 | P1: Audit log query surface | Design | Pending |
| SLA-25 | P1: Audit log query surface | Design | Pending |
| SLA-26 | P1: Audit log query surface | Design | Pending |
| SLA-27 | P1: Audit log query surface | Design | Pending |
| SLA-28 | P2: Dashboard surface | - | Pending |
| SLA-29 | P2: Dashboard surface | - | Pending |
| SLA-30 | P2: Dashboard surface | - | Pending |
| SLA-31 | P2: Report & audit export | - | Pending |
| SLA-32 | P2: Report & audit export | - | Pending |
| SLA-33 | P2: Report & audit export | - | Pending |
| SLA-34 | P3: Retention / regenerate / delivery hook | - | Pending |
| SLA-35 | P3: Retention / regenerate / delivery hook | - | Pending |
| SLA-36 | P3: Retention / regenerate / delivery hook | - | Pending |

**ID format:** `SLA-[NUMBER]`
**Status values:** Pending → In Design → In Tasks → Implementing → Verified
**Coverage:** 36 total, 0 mapped to Design/Tasks yet (spec draft). **P1 = SLA-01..27** (lifecycle 01–08,
catalog 09–17, query/isolation 18–22, audit 23–27), **P2 = SLA-28..33** (dashboard 28–30, export 31–33),
**P3 = SLA-34..36**.

---

## Success Criteria

- [ ] At period close, each tenant with services has exactly **one immutable `final` SLA report** scoring the
      committed-honored + billing dimensions from persisted evidence; re-running the lane produces **no
      duplicate** and does not mutate a `final` row.
- [ ] Every scored dimension carries a **target, measured value, status, and evidence reference**; Availability
      and added-latency-p99 appear as **`best_effort` / not-measured** with **no fabricated value**; a missing
      evidence source yields **`insufficient_data`**, never a crash.
- [ ] A tenant reads only their own **SLA** dimensions; an admin reads all tenants' reports **plus** the node
      **OLA** summary; a tenant is **never** served OLA or another tenant's report (§5.2).
- [ ] An admin can **query and export** the audit log by actor/action/target/outcome/time with **secrets
      absent**; a non-admin is refused (403); a read writes **no** `AuditEvent`.
- [ ] Reports + audit history are **exportable (CSV/JSON)** and **retention-pruned**, and the whole feature
      runs in the **worker/control-plane against existing persisted sources — zero hot-path change, no new
      data-plane surface, no fabricated numbers**.
