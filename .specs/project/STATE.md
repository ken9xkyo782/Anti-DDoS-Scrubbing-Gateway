# State

**Last Updated:** 2026-07-08
**Current Work:** M1 → **Apply-status state machine** — spec + context complete (`.specs/features/apply-status/`, APLY-01..40; D-APLY-1..3; A-APLY-1..6). Owns the `pending→queued→applying→active|failed` machine behind one guard, the API auto-enqueue (`pending→queued` + real Redis + `AgentJob` ledger, idempotent by version), the worker-facing `mark_applying/active/failed` (version-guarded, "no stale-over-new"), and the per-service apply-status read API (9.2) + admin job-list. Picks up service-rule-list's A-SRL-3 handoff (reads its `version`, **modifies** its service/rule/list services to enqueue). Awaiting approval → Design. Requires service-rule-list executed first; adds enqueue-only Redis + `AgentJob` model (worker loop = M4).
**Prior M1 work:** **Service, rule & list management (API)** — spec + context + design complete (`.specs/features/service-rule-list/`, SRL-01..44; D-SRL-1..4). Awaiting approval → Tasks. **Tenant & CIDR allocation** — spec + design + tasks complete (`.specs/features/tenant-cidr/`, TCA-01..32 → T1–T7). Awaiting approval → Execute. **Auth & RBAC** complete (T1–T12, AUTH-01..39), awaiting approval → Execute.

---

## Recent Decisions (Last 60 days)

### AD-012: Apply-status state machine policy — 3 gray areas (2026-07-08)

**Decision:** (a) **auto-enqueue** — every committed service/rule/list mutation immediately creates a job and moves the service `pending→queued`, returning **202** `{apply_status, version, active_version}` (TDD 4.5/4.6); no explicit apply/publish action in v1; (b) **M1 owns machine + guard + real Redis enqueue + `AgentJob` ledger + worker-facing `mark_applying/active/failed` (version-guarded)** — the full machine is unit+integration testable in M1 without a data-plane; M4 adds only the worker loop that calls the mark_* functions; (c) **per-service** apply targets in v1 (status/version/active_version on `ProtectedService`; scoped rule/list edits roll up to the parent service); global-blacklist/feed apply-status deferred to M4 (no generic `ApplyTarget`). Full context in `.specs/features/apply-status/context.md` (D-APLY-1..3).
**Reason:** Auto-enqueue meets ≤5s propagation by construction (idempotent-by-version collapses rapid edits); owning the whole machine now maximises what's verifiable in M1 and hands M4 a clean tested interface; per-service reuses the columns service-rule-list already added — no speculative modeling for targets with no data-plane consumer yet.
**Trade-off:** Redis becomes an enqueue-only dependency one milestone before its consumer; mark_* ship "callable, only called by tests" until M4; N rapid edits enqueue N jobs (superseded via the version guard, not cancelled); a global-blacklist edit gets no own apply-status until M4.
**Impact:** M1 Apply-status feature (APLY-01..40). **Reads** service-rule-list's `version` (A-SRL-3) and **modifies** its service/rule/list services to enqueue (mirrors the tenant-cidr `revoke` modification pattern). New `AgentJob` model + Alembic revision + enqueue-only Redis client. Flagged: A-APLY-1 Redis outage = graceful-degrade via ledger (transactional outbox), A-APLY-3 version guard is the only concurrency control (no job cancellation), A-APLY-6 retry-failed P2 / rollback (OP-05) deferred.

### AD-011: Service/rule/list management policy — 4 gray areas (2026-07-07)

**Decision:** (a) whitelist/blacklist **source** CIDRs are **arbitrary IPv4** (external allowed) — only a service's **destination** `cidr_or_ip` is scoped to `AllocatedCIDR` (AUTH-14); lists are "scoped" = attached to `service_id` (AD-003); (b) **delete service = disable-first, then cascade** its own rules/whitelist/blacklist (dangerous + audited); delete of an `enabled` service → 409; (c) service destination `cidr_or_ip` **must not overlap** another active service — enforced **globally** via partial GiST exclusion (mirrors `AllocatedCIDR`, one dst IP → one service); (d) **manual** global blacklist CRUD ships in this feature (admin-only), **feed** auto-population deferred to M4 (`source` field discriminates `manual`/`feed`). Full context in `.specs/features/service-rule-list/context.md` (D-SRL-1..4).
**Reason:** Whitelisting/blacklisting external sources is the whole point; children are composed by the service (cascade natural) while the CIDR↔service scoping relationship blocks (D-TCA-2); global service-destination no-overlap = deterministic `service_map` + unambiguous ownership; manual global-deny is plain list mgmt, the feed is its own M4 machinery.
**Trade-off:** Delete needs the explicit disable→delete sequence (no one-call live cut); services can't nest destination ranges; plan `committed`/`ceiling` are admin-only in v1 (A-SRL-1, flagged).
**Impact:** M1 Service/rule/list feature (SRL-01..44). Wires auth-rbac `AUTH-14`; realizes tenant-cidr `TCA-16` (revoke-in-use) via the dependency-count hook it stubbed. New `protected_service_active_dest_no_overlap` GiST exclusion + `(service_id,priority)` unique + ≤16-rule cap. Flagged: A-SRL-1 plan authority, A-SRL-3 apply-status handoff (stops at `pending`).

### AD-009: Tenant & CIDR allocation policy — 3 gray areas (2026-07-07)

**Decision:** (a) `AllocatedCIDR` non-overlap enforced **globally** (no two `active` allocations overlap, even within one tenant) via Postgres GiST exclusion constraint partial on `status='active'`; (b) revoking a CIDR still holding services/list entries is **blocked** (409, fail-closed) — not cascade/soft; (c) deleting a tenant with any user or active CIDR is **blocked** until emptied — `suspend` is the reversible off-switch. Full context in `.specs/features/tenant-cidr/context.md` (D-TCA-1..3).
**Reason:** Global no-overlap = one DB constraint + unambiguous scope checks (superset of PRD 7.2). Block-on-in-use / block-on-delete match the product's fail-closed posture; no orphaned users (closes AUTH-36) or silently-unprotected resources.
**Trade-off:** Admin must do explicit multi-step cleanup before revoke/delete (no one-click cascade); global overlap forbids a tenant holding nested ranges of its own.
**Impact:** M1 Tenant & CIDR feature (TCA-01..32). Resolves auth-rbac `AUTH-36`; supplies data + CIDR-scope primitive behind `AUTH-14`. Assumptions flagged: non-canonical CIDR rejected via `cidr` type; `0.0.0.0/0` rejected.

### AD-010: CIDR non-overlap = DB-level partial GiST exclusion (2026-07-07)

**Decision:** `AllocatedCIDR.cidr` uses Postgres `CIDR` type; non-overlap enforced by `EXCLUDE USING gist (cidr inet_ops WITH &&) WHERE (status='active')`. Scope containment (AUTH-14 primitive) via `cidr >>= :target`. API-layer CIDR validation via Python `ipaddress.ip_network(strict=True)` (reject IPv6/host-bits/`0.0.0.0/0`).
**Reason (verified vs PostgreSQL docs, 2026-07-07):** `inet_ops` is a **core built-in** GiST opclass (supports `&&`,`>>`,`>>=`); **no `btree_gist`/extension** needed for a single-column `&&` constraint. Must be **named explicitly** (`ops={'cidr':'inet_ops'}` in SQLAlchemy `ExcludeConstraint`) — it isn't the default opclass until PG 19. Partial `WHERE active` makes soft-revoke free the space (re-allocatable) and is race-proof (concurrent overlaps → one `ExclusionViolation` → 409).
**Trade-off:** requires the named opclass (a known SQLAlchemy gotcha); DB error must be mapped to a friendly 409.
**Impact:** M1 Tenant & CIDR `design.md`; the `cidr_in_tenant_allocation` primitive + `AllocatedCIDR` model are reused by Service/Whitelist/Blacklist (M1/M3). `User.tenant_id` FK to be pinned `ON DELETE RESTRICT`.

### AD-001: Stack for control-plane, DB, dashboard (2026-07-07)

**Decision:** API = Python + FastAPI; DB = PostgreSQL; dashboard = React (SPA). Data-plane (C/XDP/eBPF), worker (Python), queue (Redis) are PRD-fixed.
**Reason:** FastAPI shares Python with the sync worker; Postgres has native inet/cidr for CIDR allocation/overlap; React suits ≤2s realtime dashboards.
**Trade-off:** Two languages (C + Python) across the stack; React adds a build/SPA layer vs server-rendered.
**Impact:** M1 API and M5 dashboards target these; Postgres constraints back `AllocatedCIDR` non-overlap (7.2).

### AD-008: Control-plane testing/runtime conventions (2026-07-07)

**Decision:** Control-plane is **async** (asyncpg + SQLAlchemy 2.0 `AsyncSession`, `redis.asyncio`, httpx `AsyncClient`). Tests use **pytest** with `unit`/`integration` markers; integration tests run against a **docker-compose test stack** (`compose.test.yml` PG+Redis). Quick gate = **ruff + mypy + unit**; full gate adds integration. Conventions in `.specs/codebase/TESTING.md`.
**Reason:** Async fits later realtime dashboards/worker; real PG needed for citext/JSONB/CHECK fidelity; ruff+mypy modern default.
**Trade-off:** Integration tests not parallel-safe (shared compose stack) → mostly sequential execution.
**Impact:** All control-plane code (M1–M6) follows async idioms; only unit-tested tasks can be `[P]`.

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
