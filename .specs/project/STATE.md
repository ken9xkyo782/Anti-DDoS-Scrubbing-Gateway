# State

**Last Updated:** 2026-07-07
**Current Work:** M1 → Auth & RBAC — spec + design complete (`.specs/features/auth-rbac/`, AUTH-01..39; Redis sessions + reusable RBAC guard & audit writer), awaiting approval → Tasks

---

## Recent Decisions (Last 60 days)

### AD-001: Stack for control-plane, DB, dashboard (2026-07-07)

**Decision:** API = Python + FastAPI; DB = PostgreSQL; dashboard = React (SPA). Data-plane (C/XDP/eBPF), worker (Python), queue (Redis) are PRD-fixed.
**Reason:** FastAPI shares Python with the sync worker; Postgres has native inet/cidr for CIDR allocation/overlap; React suits ≤2s realtime dashboards.
**Trade-off:** Two languages (C + Python) across the stack; React adds a build/SPA layer vs server-rendered.
**Impact:** M1 API and M5 dashboards target these; Postgres constraints back `AllocatedCIDR` non-overlap (7.2).

### AD-002: Service `disabled` = drop-all (D1 / BL-03) (2026-07-07)

**Decision:** Disabling a service drops all its traffic with reason `service_disabled` (distinct from `service_miss`), requires UI confirm + audit. NOT pass-through.
**Reason:** Inline inbound-only bridge; disable is an intentional protection cut, not a bypass.
**Impact:** M2 pipeline + M1 UI confirm/audit. Refs 6.3, 8.2, 10.2, 12.2.

### AD-003: Whitelist bypass is service-scoped (D2 / BL-01, BL-02) (2026-07-07)

**Decision:** Whitelist/VIP bypass keyed by `service_id`+source CIDR; never edits the global blacklist/feed map. Whitelisting a feed IP raises alert+audit; admin flag can forbid it.
**Reason:** Preserve tenant isolation (5.2) — tenant A must not remove global protection for B/C.
**Impact:** `whitelist_lpm` key includes `service_id` (M3). Refs 6.5, 6.7, 8.3, 12.3.

### AD-004: Allow-rule = first-match by priority, terminal (D3 / BL-05) (2026-07-07)

**Decision:** First enabled rule matching by ascending `priority` decides the verdict; if it is out of quota → `rate_limit_drop`, no fall-through to looser rules.
**Reason:** Fall-through would empty per-rule limits (traffic spills to a looser rule).
**Impact:** M3 rule loop; UI warns on overlapping rules. Refs 6.4, 8.2, 12.2.

### AD-005: Atomic config swap via double-buffer `active_slot` (BL-06) (2026-07-07)

**Decision:** Config maps versioned into 2 slots; worker builds/verifies the inactive slot, then flips `active_slot` in one write. Data-plane pins the slot at ingress. Runtime-state maps are unslotted. Rollback = flip back.
**Reason:** Avoid hybrid new-rule/old-service windows; enables instant rollback (OP-05).
**Impact:** M4 worker + M2 ingress pin. Refs 8.1, 8.3.

### AD-006: Chargeback by p95 clean Gbps (D5 / CM-03, BL-09) (2026-07-07)

**Decision:** Internal chargeback metered on clean (redirected) bandwidth; `billed_gbps = max(committed_clean_gbps, p95_clean_gbps)`; `ServicePlan`/`BillingUsage` model it. Billing bytes from exact per-CPU counters, separate from rate-limited event sampling.
**Reason:** p95 is the bandwidth-billing industry norm; sampling can drop events and must not be trusted for money.
**Impact:** M5 metering + M3 ceiling enforcement (`service_ceiling_drop`). Refs 7.1, 10.3, 8.2/8.3, 12.6.

### AD-007: SLA option (ii) — Availability excluded at Pilot (D6 / CM-01, CM-04) (2026-07-07)

**Decision:** Per-tenant SLA level = high on latency/accuracy/propagation/fairness; Availability deliberately **excluded** from the Pilot SLA (best-effort + maintenance window + bypass in the OLA). HA is the GA condition for an Availability commitment.
**Reason:** Single-node fail-closed inline = SPOF; committed clean bandwidth is guaranteed in hardware terms, availability is not.
**Impact:** M6 bypass/OLA; M7 HA. Refs 3.2, 11.4, 8.4, 14.

---

## Active Blockers

### B-001: No HA / single-node SPOF (CM-01) — GA Blocker

**Discovered:** 2026-07-07 (PRD BA review)
**Impact:** Blocks a production Availability SLA; does NOT block Pilot or development.
**Workaround (Pilot):** OLA documents maintenance window + bypass procedure (OP-03, M6); Availability excluded from Pilot SLA (AD-007).
**Resolution:** Active/passive HA + link bypass (fail-to-wire) at GA — M7.

---

## Lessons Learned

_(none yet)_

---

## Quick Tasks Completed

| # | Description | Date | Commit | Status |
| --- | --- | --- | --- | --- |

---

## Deferred Ideas

Ideas for future features/phases (mostly PRD-tracked GA/Backlog findings):

- [ ] Auto-response / one-click mitigate for fast attack reaction (OP-02, GA) — Captured during: init
- [ ] Monitor/count-only rule mode before enforcing (OP-04, GA) — Captured during: init
- [ ] Sampled per-tenant drop-flow records for self-service debug (OP-06, GA) — Captured during: init
- [ ] `expires_at` reconciliation sweep for whitelist/blacklist (BL-07, GA) — Captured during: init
- [ ] Stateless SYN-cookie / scan detection to back SYN-flood/port-scan claims (BL-04, GA) — Captured during: init
- [ ] PII retention/anonymization for `top_src` (CM-08, GA) — Captured during: init
- [ ] Multi-admin & separation of duties (OP-07, GA) — Captured during: init
- [ ] Guided onboarding + learning mode (OP-08, Backlog) — Captured during: init
- [ ] SSO/IdP + MFA for admin (CM-10, Backlog) — Captured during: init

---

## Todos

Open before Pilot (non-engineering, non-blocking — Product/Legal owned):

- [ ] CM-02: IPv6 hard-drop blackhole warning + checklist in onboarding
- [ ] CM-06: capacity positioning (single 40G node = small/mid scrubber; absorption depends on upstream)
- [ ] CM-07: review threat-feed licenses for commercial/internal-paid use

---

## Preferences

**Model Guidance Shown:** never
