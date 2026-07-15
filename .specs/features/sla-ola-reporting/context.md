# SLA/OLA Reporting & Audit Context

**Gathered:** 2026-07-14
**Spec:** `.specs/features/sla-ola-reporting/spec.md`
**Status:** Ready for design

---

## Feature Boundary

M6 #3 (final M6 feature). A **control-plane/worker-only** reporting & audit surface that (1) materializes a
per-tenant, per-period **SLA report** (met/missed per dimension, tied to the `BillingUsage` period) plus an
admin **OLA operational summary**, from **already-persisted evidence only**, and (2) exposes an **admin-only
queryable/exportable view over the existing audit log**. **Zero hot-path change, no new data-plane surface,
no new counters, no fabricated numbers.** Commitments with no Pilot measurement source (Availability,
added-latency p99) are **disclosed as best-effort, not scored**. The audit **write** path is already complete
(41 `record_event` sites); this feature adds only the **read/query/export** half.

---

## Implementation Decisions

### D-SLA-1 — Report compute & storage: worker lane → immutable period rows

- A new **worker background lane** (like the executed `billing`/`telemetry`/`alert` lanes; **not** a Redis
  `JobType`/`AgentJob`) materializes reports.
- Lifecycle mirrors `BillingUsage`: a running **`open`** report per (tenant, period) updated each tick, frozen
  to an **immutable `final`** row (`finalized_at`) at period close. Idempotent, unique `(tenant, period_start)`.
- The API/export **read stored rows** and **never recompute** scoring on read (materialization is the only
  writer). Restart-safe by construction.
- Period = **UTC calendar month, aligned to the `BillingUsage` period** (reuse the executed
  `services/billing_period.py` UTC-month arithmetic — no new period logic).

### D-SLA-2 — Dimensions: measurable-only, derived from persisted evidence

- **Scored SLA dimensions (tenant-facing, per service → owning tenant):**
  - **Committed-clean-bandwidth honored** (the one hard SLA, AD-007) — met/missed derived from persisted
    **fairness-breach `Alert`** instances (Alerting M6 #2) + their firing→resolved durations within the
    period (breach-seconds / count / honored-fraction vs a tunable target). *Soft-coordination source* — if
    Alerting is not executed, this dimension = `insufficient_data` (A-SLA-5), never fabricated.
  - **Billing / chargeback** — reads the finalized `BillingUsage` (`billed_gbps = max(committed, p95)`,
    `overage_gbps`, `overage_policy`); **never recomputes p95** (immutable billing row is the source).
- **Scored OLA dimensions (node/admin):**
  - **Config-propagation reliability** — count of `AgentJob` → `failed` in the period vs a tunable target.
  - **Feed-sync health** — `FeedSyncRun` failures per source over the period.
  - **Bypass/maintenance conduct** — bypass + maintenance windows (count + total duration) from
    `node.bypass.*`/`node.maintenance.*` `AuditEvent`s + `NodeControl`; zero windows if bypass-maintenance
    not executed (not an error). Discloses the chargeback-exclusion note (A-CHG-8).
- **Best-effort / not-scored disclosures (never met/missed, never a fabricated value):**
  - **Availability** — single-node SPOF, best-effort at Pilot (AD-007).
  - **Added-latency p99 ≤ 1 ms** — no in-band measurement path in v1 (A-TEL-8).
- **Status vocabulary:** `met` / `missed` / `best_effort` / `insufficient_data`. Every scored dimension
  records **target + measured value + status + evidence reference**. Admin-tunable targets; documented
  defaults otherwise.

### D-SLA-3 — Audit half: admin-only read/query/export surface (no write-path change)

- The audit **write path + dangerous-action taxonomy are already complete** (verified in-tree: 41
  `record_event` call sites incl. `tenant.delete`, `service.delete`/`service.enable`, `feed.delete`,
  `node.bypass.*`, `node.maintenance.*`; `scrub_metadata` strips password/token/secret/credential). This
  feature adds **no new write** and **no new coverage**.
- Add an **admin-only** `GET /audit` (paginated newest-first via `ix_audit_events_created_at`) + filters
  (actor / action / target_type/target_id / outcome / time range) + CSV/JSON export.
- **Admin-only** is the structural fit: `AuditEvent` has **no `tenant_id`** (actor-oriented, mirrors the
  Alerting D-033-2 "no email column" finding). **Tenant self-audit is deferred** (would need a derived
  tenant-scope column + backfill). Tenants get the SLA **report**; admins get the audit **log**.

### D-SLA-4 — Delivery/format & SLA vs OLA split

- **SLA report** = tenant-facing per-period met/missed via **API + CSV/JSON export + a P2 SPA panel**
  (reusing the executed billing/telemetry RBAC + export + `theme/thresholds.ts` display-coloring patterns).
- **OLA summary** = an **admin-scoped operational section** (apply reliability, feed health, bypass/
  maintenance windows) in the same report family — never served to a tenant (§5.2).
- **No automated email in v1.** A scheduled-delivery **hook/seam** that could later reuse Alerting's SMTP
  channel is a **P3 forward artifact only** (SLA-36), not built.

### Agent's Discretion

- Exact report **table/model shape** (single `SlaReport` row + a `SlaReportDimension` child table vs a
  JSONB dimension array; separate SLA vs OLA rows vs one row with a scope classifier), lane **tick cadence**,
  and the precise **committed-honored math** (breach-seconds vs honored-fraction vs breach-count) — Design's
  call, grounded against the real `Alert`/`BillingUsage`/`AgentJob`/`FeedSyncRun`/`NodeControl` fields.
- Retention horizon default, export column contract, and `period` param shape (`YYYY-MM`) — follow the
  billing/telemetry precedent; confirm at Design/Tasks.

---

## Assumptions (A-SLA-1..8)

- **A-SLA-1** — Reporting lane is a **new in-worker background asyncio task** (billing/telemetry/alert
  precedent), **not** a Redis `JobType`; catch-log-continue, idempotent per (tenant, period).
- **A-SLA-2** — **Zero data-plane change, no new counter** — reads only already-persisted rows
  (`Alert`, `BillingUsage`, `AgentJob`, `FeedSyncRun`, `NodeControl`, `AuditEvent`).
- **A-SLA-3** — New **additive** models (`SlaReport` [+ dimension detail], possibly a target-override table)
  + migration after the current head **`20260714_0011_alerting`** → this feature is **`_0012`** (`down_revision`
  pinned live at Execute). No change to M1–M5 schema.
- **A-SLA-4** — All evidence sources are **already present in-tree** (verified live): `BillingUsage` +
  migration `_0009` (chargeback executed), `NodeControl` `_0010`, Alerting models `_0011` (evaluator lane
  landing now). So the whole report + audit surface is **buildable now** — the committed-honored dimension
  simply reads `insufficient_data` until fairness-breach `Alert` rows exist. No hard external gate remains.
- **A-SLA-5** — **Never fabricate.** A dimension with no evidence source for the period = `insufficient_data`;
  a not-yet-executed feature (Alerting fairness-breach, bypass-maintenance windows) degrades that one
  dimension only, never the whole report or the lane (fail-safe, bounded).
- **A-SLA-6** — **Committed-honored evidence = persisted fairness-breach `Alert` instances** (Alerting M6 #2,
  soft coordination). No new "committed-honored" DP/telemetry counter is introduced (rejected option).
- **A-SLA-7** — **Audit read is admin-only, read-only**; secrets are already scrubbed at write, never
  re-exposed on read/export; the read path writes no `AuditEvent`.
- **A-SLA-8** — Reports + audit query/export reuse M1 session/RBAC verbatim (`require_admin`,
  `load_service_for_principal`→404, `Principal.tenant_id`); tenant scope via the report's denormalized
  `tenant_id` (billing precedent), since `AuditEvent` itself carries no tenant.

---

## Specific References

- **`BillingUsage` open→final immutable lifecycle** (M5 chargeback, `models.py:611`) is the explicit template
  for the SLA report's `open`→`final` + `finalized_at` + denormalized `tenant_id`/`service_name` snapshot
  (SET NULL survives service deletion).
- **Alerting `Alert`** rows (M6 #2, `pending→firing→resolved`, `fired_at`/`resolved_at`) are the
  committed-honored evidence source (fairness-breach rule ALRT-16).
- **`AuditEvent`** (`models.py:435`) + `services/audit.py` `record_event`/`scrub_metadata` — the complete
  write path this feature reads; `ix_audit_events_created_at` backs newest-first pagination.
- **`services/billing_period.py`** UTC calendar-month arithmetic — reused for the report period boundary.
- Export + retention-prune shape follows the executed **telemetry/billing/alert** patterns.

---

## Deferred Ideas

- **Per-tenant self-service audit trail** — needs a derived tenant-scope column on `AuditEvent` + backfill;
  deferred (D-SLA-3). Revisit with multi-admin / separation-of-duties (OP-07).
- **Scheduled email delivery** of finalized reports (reuse Alerting SMTP) — v1 ships only the hook (SLA-36).
- **Availability / added-latency-p99 as real scored SLAs** — blocked on HA (CM-01, GA) and an in-band latency
  measurement path respectively; Pilot discloses them best-effort only.
- **Custom / tenant-defined SLA targets or report dimensions** — v1 is a fixed catalog with admin-tunable
  targets.
- **Clean-accuracy per-tenant scoring** — only measurable against the v1 test set; not a per-period dimension.
