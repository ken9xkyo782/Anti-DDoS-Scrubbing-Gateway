# Chargeback Metering — Context (Gray-Area Decisions)

Decisions captured during Specify for gray areas with multiple valid approaches. Resolved via
AskUserQuestion (2026-07-10). Referenced by `spec.md` and carried into Design.

## Decisions

### D-CHG-1: p95 samples come from a dedicated coarse billing series, not telemetry windows

**Question:** p95 clean-Gbps must be computed over a full billing period (~1 month), but the
*Telemetry & dashboards* `TelemetryCounter` windows are ≤ 2 s and pruned on a short retention horizon
(~1.3 M rows/service/month if kept). Where do the p95 samples come from?

**Decision:** Introduce a **dedicated per-service billing sample series** (`BillingSample`): the worker
records one exact clean-bps sample per service at a **billing cadence (default 300 s / 5 min)**, retained
for the full billing period. p95 is computed over these samples. It is decoupled from the telemetry 2 s
windows and their pruning.

**Reason:** 5-minute sampling with a 95th-percentile roll-up is the industry standard for "burstable"
bandwidth billing; it keeps storage bounded (~8,640 rows/service/month) and auditable, and does not couple
billing accuracy to telemetry's short retention. Reusing the 2 s telemetry rows would explode row volume
and force long telemetry retention purely for billing; a streaming/approximate t-digest would be cheap but
not auditable/replayable.

**Impact:** New `BillingSample` model (per-service, `sample_ts`, `clean_bps`), retention pruned after the
owning period finalizes. Reads the **exact per-service `clean_bytes` counter** introduced by D-TEL-1 — no
new data-plane counter. Cadence is node-configurable (`worker_billing_*` setting).

### D-CHG-2: Worker sampler + scheduled period-close rollup writes an immutable `BillingUsage`

**Question:** How and when is the `BillingUsage` record produced — a continuous sampler + period-close
job, or computed on-demand at `GET /billing/usage`?

**Decision:** The worker **continuously appends** billing samples (D-CHG-1); a **scheduled in-worker
period-close job** computes p95 / billed / overage and writes the **immutable** `BillingUsage` for the
closed period. A **running current-period estimate** row (`status = open`) is refreshed each cadence for
showback. The API reads stored rows — it does not recompute p95 on read.

**Reason:** Mirrors the executed telemetry aggregator + windowed-rows pattern (a background asyncio lane,
not a Redis `JobType`). Keeps p95 authoritative and immutable per period (finance can re-read a settled
figure), survives sample-retention pruning (samples can be pruned once the period is finalized), and avoids
recomputing a month of percentiles on every API call. On-demand computation would be simpler but
non-immutable, non-auditable, and sensitive to retention.

**Impact:** New in-worker background task (`BillingSampler`) + a period-close rollup (checked at each tick;
finalizes any period whose boundary has passed). `BillingUsage.status ∈ {open, final}`; unique
`(service_id, period_start)`; re-run idempotent. No Redis `JobType`/`AgentJob` (like telemetry, supersedes
any ledger-write posture).

### D-CHG-3: Billing period = UTC calendar month, no proration

**Question:** How is the billing period defined — calendar month, rolling 30 days, or configurable?

**Decision:** Periods are **UTC calendar months** (default **monthly**). A service created mid-period simply
has fewer samples (p95 tolerates sparsity); **no proration**. Period length is a node-level setting kept
forward-compat for later (weekly/custom), but v1 ships calendar-month monthly only.

**Reason:** Calendar-month aligns with how internal chargeback/showback is reported; p95 naturally handles
partial periods (it is a percentile of whatever samples exist), so proration adds bookkeeping without
changing the metered figure materially at pilot. Rolling windows complicate boundary/close semantics;
per-tenant configurable periods multiply boundary bookkeeping now.

**Impact:** Period boundaries derived from UTC month arithmetic; `period_start`/`period_end` on
`BillingUsage`. Mid-period create/delete → partial sample set, `sample_count` exposes confidence. A
node-level `billing_period` knob exists but is fixed to `monthly` in v1.

### D-CHG-4: Per-service billing; VIP/whitelist clean included; bypass excluded

**Question:** What clean traffic is billable, and at what granularity — per-service or per-tenant, and does
VIP/whitelisted clean count?

**Decision:** **One `BillingUsage` per service** (1:1 with `ServicePlan`). **All** clean bytes redirected
`IN→OUT` for the service count as billable — **including VIP/whitelisted clean** (it is delivered clean
bandwidth, already counted at the `redirect_out()` choke point by the D-TEL-1 counter). **Bypass-mode**
traffic is **excluded** from billing (M6 — the bypass flag does not exist yet; forward dependency).

**Reason:** `ServicePlan` (committed/ceiling/billing_metric/overage_policy) is per-service, so billing is
naturally per-service; `billed_gbps = max(committed, p95)` is defined against the per-service plan. VIP
clean is bandwidth the gateway delivered, so it is billable; excluding it would require a separate non-VIP
data-plane counter for no product benefit at pilot. Bypass traffic is explicitly "counted separately"
(TDD §4.9) and must not be billed once bypass exists.

**Impact:** `BillingUsage.service_id` FK (`SET NULL` to preserve history) + denormalized `tenant_id` and
service-name snapshot for attribution after deletion. Billable byte source = the single exact per-service
`clean_bytes` counter (VIP + rule-admitted alike). When M6 bypass lands, it must exclude bypass clean from
this counter's billable interpretation (documented forward dependency, A-CHG-8).

## Assumptions (validate in Design)

- **A-CHG-1:** Chargeback **reuses the exact per-service `clean_bytes` counter** from *Telemetry &
  dashboards* (D-TEL-1) as the billing byte source — it adds **no** new data-plane counter. This feature is
  therefore **Execute-gated on telemetry-dashboards executed** (the counter + `dp_id` contract), the same
  plan-ahead posture telemetry had relative to M4 #2. The control-plane billing model/API can be built
  ahead against a fake reader.
- **A-CHG-2:** The billing sampler and period-close rollup are **new in-worker background asyncio tasks**
  (the feed-sync / telemetry-aggregator lane pattern), **not** Redis `JobType`s / `AgentJob`s — no ledger
  writes at billing cadence.
- **A-CHG-3:** Samples are read via the **same C-helper / `dpstat snapshot --json` reader** telemetry
  introduces (A-TEL-1) — no new privileged surface on the gateway node.
- **A-CHG-4:** New **additive** Postgres models `BillingSample` + `BillingUsage` (+ migration); **no change**
  to M1–M4 schema. `ServicePlan.committed_clean_gbps` / `billing_metric` / `overage_policy` are reused
  verbatim; Gbps stored as `Numeric(10,2)` to match `ServicePlan`.
- **A-CHG-5:** The per-service clean-byte counter is **unslotted** runtime state (untouched by the
  double-buffer swap); the sampler reconciles stale `dp_id` keys against the set of active services, exactly
  like the telemetry aggregator (A-TEL-6).
- **A-CHG-6:** `GET /billing/usage` (+ export/history) reuse the existing session/RBAC middleware and the
  `load_service_for_principal` tenant-ownership guard **verbatim** (tenant = own services → 404 on
  cross-tenant; admin = node-wide). No new auth mechanism.
- **A-CHG-7:** v1 implements **only** the `p95_clean_bps` metric; the `billing_metric` column stays
  forward-compat for other metrics. `overage_policy` (`billed`/`capped`) is snapshotted per period and
  governs finance interpretation of `overage_gbps`, not the `billed = max(committed, p95)` formula.
- **A-CHG-8:** Bypass-mode "count separately" (TDD §4.9) is **out of scope** (M6). When global bypass lands,
  bypass clean must be excluded from the billable clean-byte interpretation — a documented forward
  dependency, not built here.
