# Service, Rule & List Management (API) Specification

**Milestone:** M1 — Control-plane foundation & tenant model
**Category ID:** SRL
**Status:** Spec drafted; awaiting approval → Design
**Depends on:**
- **Auth & RBAC** (`.specs/features/auth-rbac/`) — consumes `require_admin`, `get_current_user`, the tenant-ownership guard (`authorize_tenant_resource`/`scope_to_tenant`), and `audit.record_event` unchanged; **wires** its deferred `AUTH-14` (CIDR-scope on writes) concretely.
- **Tenant & CIDR allocation** (`.specs/features/tenant-cidr/`) — consumes `AllocatedCIDR`, the `cidr_in_tenant_allocation` primitive / `require_within_allocation` guard, and `core/cidr`; **realizes** the dependency-count hook that tenant-cidr stubbed for `TCA-16` (revoke-in-use). Requires tenant-cidr executed first.

**Discuss context:** `.specs/features/service-rule-list/context.md` (D-SRL-1..4, A-SRL-1..6)

## Problem Statement

A tenant with allocated IP space cannot yet declare *what* to protect or *how*. This feature delivers
the per-service protection config that everything downstream consumes: `ProtectedService` (+ its 1:1
`ServicePlan`), `AllowRule`, service-scoped whitelist/VIP, service/tenant blacklist, and admin manual
global blacklist. It is the control-plane's config **source of truth** (PRD 4.7, 6.1–6.5); M2/M3 build
the data-plane maps from these rows, and the Apply-status feature propagates them. Like tenant-cidr, it
is pure control-plane persistence + validation + tenant/CIDR scoping + audit — the first real consumer
of tenant-cidr's `cidr_in_tenant_allocation` primitive (satisfying `AUTH-14`) and the feature whose
rows make tenant-cidr's revoke-in-use rule (`TCA-16`) enforceable.

## Goals

- [ ] `ProtectedService` + 1:1 `ServicePlan` CRUD, tenant-scoped, with destination `cidr_or_ip` **⊆ the
      tenant's active `AllocatedCIDR`** (AUTH-14) and **no destination overlap** across active services
      (D-SRL-3), every mutation audited (PRD 6.3, 7.2, 11.2).
- [ ] `AllowRule` CRUD with the PRD constraints: **≤16 rules/service**, **`priority` unique within a
      service**, protocol/port-range/pps/bps validation, and a **soft overlap warning** (first-match by
      priority is terminal — AD-004, so overlaps are warned not blocked) (PRD 6.4).
- [ ] Whitelist/VIP CRUD (service-scoped bypass keyed by `service_id`+**arbitrary IPv4 source**, AD-003),
      with the service-level VIP ceiling fields; service/tenant blacklist CRUD; **admin manual global
      blacklist** CRUD (D-SRL-4) (PRD 6.5).
- [ ] Enable/disable with **drop-all** semantics + **confirm + audit** (AD-002); **delete = disable-first
      then cascade children** as a dangerous, audited action (D-SRL-2).
- [ ] Strict tenant isolation on every read/write; admin cross-tenant with owner annotation (AUTH-13/15);
      zero cross-tenant leakage (PRD 5.2).
- [ ] Realize tenant-cidr's dependency hook: revoking an `AllocatedCIDR` that still contains an active
      service is refused (closes the `TCA-16` stub).

## Out of Scope

Explicitly excluded to prevent scope creep.

| Feature | Reason |
| --- | --- |
| BPF map build / bloom / LPM / `service_map` / `rule_block_map` representation | Data-plane, M2/M3; this feature only persists the config rows they are built from |
| Apply-status state machine (`pending→queued→applying→active→failed`) + Redis enqueue | Separate M1 *Apply-status* feature; this feature stops at `apply_status=pending` (A-SRL-3) |
| Threat-feed auto-population of global blacklist + "whitelist overlaps feed IP → alert+audit" | M4 *Threat intelligence feed sync*; no feed exists yet (D-SRL-4, AD-003) |
| Fairness/rate-limit *enforcement*, VIP-ceiling *enforcement*, `service_ceiling_drop` | Data-plane M3; this feature only stores the committed/ceiling/pps/bps/vip values |
| `BillingUsage` / p95 metering | M5 chargeback; this feature only stores `ServicePlan.billing_metric`/`overage_policy` |
| `expires_at` reconciliation sweep for lists | GA (BL-07); no `expires_at` in v1 (A-SRL-6) |
| Tenant self-service plan sizing | Plan committed/ceiling are admin-only in v1 (A-SRL-1); relaxation deferred |
| Additional service `mode`s | v1 uses `allow-rule-only` only (A-SRL-2) |

---

## User Stories

### P1: ProtectedService + ServicePlan CRUD (scoped, non-overlap) ⭐ MVP

**User Story**: As a `tenant_user` (own tenant) or `admin` (any tenant), I want to declare a protected
service on an IP/CIDR I'm entitled to, with a plan, so that clean traffic to it can later be forwarded.

**Why P1**: The central object of the feature (PRD 6.3, 7.2); every rule/list/telemetry/billing row
hangs off it, and it is the concrete consumer of AUTH-14.

**Acceptance Criteria**:

1. WHEN a caller creates a service with a `name`, a destination `cidr_or_ip`, `mode=allow-rule-only`, optional VIP ceiling (`vip_pps`/`vip_bps`), and a `ServicePlan`, AND `cidr_or_ip` is fully contained in an **active** `AllocatedCIDR` of the owning tenant THEN the system SHALL persist the `ProtectedService` (`enabled=false` default, `apply_status=pending`, `version=1`) with its 1:1 `ServicePlan`, and audit it. `(SRL-01)` *(A-SRL-2, A-SRL-3)*
2. WHEN a caller creates a service whose `cidr_or_ip` is NOT fully within an active `AllocatedCIDR` of the owning tenant THEN the system SHALL reject (403/422 per `require_within_allocation`), write nothing — using the reusable `cidr_in_tenant_allocation` primitive, not a reimplementation. `(SRL-02)` *(AUTH-14, TCA-20)*
3. WHEN a `ServicePlan` is created/edited with `committed_clean_gbps > ceiling_clean_gbps` THEN the system SHALL reject with 422 and persist nothing. `(SRL-03)`
4. WHEN a caller creates/edits a service whose `cidr_or_ip` overlaps any other **active** service's destination (same or different tenant — global, D-SRL-3) THEN the system SHALL reject with 409 and name the conflicting service. `(SRL-04)`
5. WHEN a caller lists/views services THEN the system SHALL return each with its plan, `enabled`, `apply_status`, and `version`; an `admin` sees all tenants' services each annotated with owning tenant/creator (AUTH-15); a `tenant_user` sees only their own. `(SRL-05)`
6. WHEN a caller edits a service's `name`, `cidr_or_ip` (re-checked ⊆ allocation + non-overlap), VIP ceiling, or `mode` THEN the system SHALL apply it, bump `updated_at` and `version`, reset `apply_status=pending`, and audit it. `(SRL-06)`
7. WHEN a `tenant_user` attempts to set or raise `committed_clean_gbps`/`ceiling_clean_gbps` THEN the system SHALL reject with 403 (plan sizing is **admin-only**); a tenant-created service SHALL receive a default plan an `admin` later sizes. `(SRL-07)` *(A-SRL-1, confirmed 2026-07-07)*
8. WHEN a caller submits an IPv6, malformed, or host-bits-set `cidr_or_ip` THEN the system SHALL reject with 422 (reusing `core/cidr`), naming the canonical form where applicable. `(SRL-08)`

**Independent Test**: Allocate `203.0.113.0/24` to acme; acme user creates service on `203.0.113.10/32` (audited, `pending`, disabled); create on `198.51.100.0/24` (outside allocation) → 403; create on `203.0.113.0/25` overlapping the first → 409 naming it; `committed=5,ceiling=2` → 422; tenant_user setting `committed` → 403.

---

### P1: Enable / disable service (drop-all, confirm + audit) ⭐ MVP

**User Story**: As a service owner, I want to disable a service as an explicit, audited action, so that
cutting protection is never accidental.

**Why P1**: AD-002/BL-03 — disabling is drop-all (a protection cut, not a bypass) and PRD 11.2 lists it
as a dangerous, audit-required action distinguished from `service_miss`.

**Acceptance Criteria**:

1. WHEN a caller disables a service (`enabled=false`) with an explicit confirm THEN the system SHALL persist the change, reset `apply_status=pending`, and audit it as a **dangerous action** — the resulting data-plane behaviour is `service_disabled` drop-all (enforced M2), NOT pass-through. `(SRL-09)` *(AD-002)*
2. WHEN a caller re-enables a service (`enabled=true`) THEN the system SHALL persist it, reset `apply_status=pending`, and audit it. `(SRL-10)`
3. WHEN a caller disables an already-disabled service (or enables an already-enabled one) THEN the system SHALL treat it as an idempotent no-op and SHALL NOT write a duplicate audit event for a non-change. `(SRL-11)`

**Independent Test**: Disable a service → `enabled=false`, one dangerous-action audit row, `apply_status=pending`; disable again → no second audit row; re-enable → audited.

---

### P1: Delete service — disable-first, cascade children (dangerous + audited) ⭐ MVP

**User Story**: As a service owner, I want deleting a service to require it be disabled first and to clean
up its own rules and lists, so that a delete is never a surprise live cut and never orphans children.

**Why P1**: D-SRL-2; PRD 11.2 dangerous action; prevents orphaned child rows and enables the CIDR to be
revoked afterward.

**Acceptance Criteria**:

1. WHEN a caller deletes a service that is still `enabled` THEN the system SHALL refuse with 409 ("disable first"), change nothing, and audit the refusal. `(SRL-12)`
2. WHEN a caller deletes a `disabled` service THEN the system SHALL hard-delete it AND cascade-delete its `AllowRule`s, whitelist entries, and service-blacklist entries, and audit the deletion as a dangerous action (one event; children not individually re-audited). `(SRL-13)`
3. WHEN a service deletion is refused or performed THEN the outcome (`denied`/`success`) SHALL be audited. `(SRL-14)`

**Independent Test**: Create service + 2 rules + 1 whitelist; delete while enabled → 409; disable; delete → 204, service + all children gone, one dangerous-action audit row.

---

### P1: AllowRule CRUD (≤16, unique priority, overlap warning) ⭐ MVP

**User Story**: As a service owner, I want to define ordered allow-rules with per-rule rate limits, so
that clean traffic is admitted by first-match priority and everything else is dropped.

**Why P1**: PRD 6.4 core allowlist behaviour; the ≤16 / unique-priority / first-match constraints
directly shape the M3 data-plane rule loop (AD-004).

**Acceptance Criteria**:

1. WHEN a caller adds a rule (`priority`, `protocol`, `src_port_range`, `dst_port_range`, `pps`, `bps`, `enabled`) to a service they own THEN the system SHALL persist it, bump the service `version`, reset `apply_status=pending`, and audit it. `(SRL-15)`
2. WHEN a caller adds/edits a rule with a `priority` already used by another rule **in the same service** THEN the system SHALL reject with 409 (unique within `service_id`). `(SRL-16)`
3. WHEN a caller adds a 17th rule to a service that already has 16 THEN the system SHALL reject with 409 (`≤16` cap). `(SRL-17)`
4. WHEN a caller adds/edits a rule whose (`protocol`, port-range) overlaps an existing rule on the same service THEN the system SHALL **succeed** and return a non-blocking **overlap warning** naming the overlapped rule(s) — first-match by ascending `priority` is terminal, so overlap is advisory (AD-004). `(SRL-18)`
5. WHEN a caller submits an invalid port range (start > end, or outside 0–65535) or an unsupported protocol THEN the system SHALL reject with 422. `(SRL-19)`
6. WHEN a caller lists/views/edits/deletes rules THEN the system SHALL scope them to the owning service and deny cross-tenant access (404/403, no leak). `(SRL-20)`
7. WHEN any rule is created/edited/deleted THEN the system SHALL audit it and bump the parent service `version`. `(SRL-21)`

**Independent Test**: Add rules at priority 10, 20 (audited, `version` bumps); add another at 10 → 409; add 15 more to reach 16 then one more → 409; add a rule overlapping rule-10's ports → 200 + warning; `dst_port_range=80-79` → 422.

---

### P1: Whitelist / VIP CRUD (service-scoped, arbitrary source) ⭐ MVP

**User Story**: As a service owner, I want to whitelist trusted source IPs for a service so their traffic
bypasses filtering (subject to a VIP ceiling), without affecting any other service.

**Why P1**: PRD 6.5; AD-003 keeps bypass service-scoped (no cross-service, no global-map edit) — core to
tenant isolation.

**Acceptance Criteria**:

1. WHEN a caller adds a whitelist entry with an **arbitrary valid IPv4** `source_cidr` to a service they own THEN the system SHALL persist it keyed by (`service_id`, `source_cidr`) (AD-003), reset `apply_status=pending`, and audit it. `(SRL-22)` *(D-SRL-1)*
2. WHEN a caller submits an IPv6 or malformed `source_cidr` THEN the system SHALL reject with 422; the source is NOT required to be within the tenant's `AllocatedCIDR` (external sources allowed). `(SRL-23)` *(D-SRL-1)*
3. WHEN a service's VIP ceiling (`vip_pps`/`vip_bps`) is set THEN the system SHALL persist it on the service as the aggregate ceiling governing all whitelisted traffic (enforced M3); a whitelist entry never edits any global/blacklist map (AD-003). `(SRL-24)`
4. WHEN a caller lists/deletes whitelist entries THEN the system SHALL scope them to the owning service and deny access to another tenant's or service's entries (404/403). `(SRL-25)`

**Independent Test**: Add whitelist `198.51.100.7/32` (external) to acme's service → 200 + audit; add `2001:db8::/48` → 422; beta user reading acme's whitelist → 404.

---

### P1: Service/tenant blacklist CRUD (service-scoped, arbitrary source) ⭐ MVP

**User Story**: As a service owner, I want to blacklist source IPs for my service, so known-bad sources
are dropped for that service.

**Why P1**: PRD 6.5 tenant/service blacklist; the service-scoped counterpart to the global list.

**Acceptance Criteria**:

1. WHEN a caller adds a service-blacklist entry with an **arbitrary valid IPv4** `source_cidr` (`scope=service`) to a service they own THEN the system SHALL persist it, reset `apply_status=pending`, and audit it. `(SRL-26)` *(D-SRL-1)*
2. WHEN a caller submits IPv6/malformed source THEN the system SHALL reject 422; and list/delete of service-blacklist entries SHALL be scoped to the owning service and cross-tenant-denied. `(SRL-27)`

**Independent Test**: Add service-blacklist `45.0.0.0/8` to acme's service → 200 + audit; IPv6 → 422; beta cannot list acme's blacklist.

---

### P1: Global blacklist — admin manual CRUD ⭐ MVP

**User Story**: As an `admin`, I want to manually add/remove global blacklist entries that apply to all
services, so I can block a known-bad source node-wide without waiting for the feed.

**Why P1**: PROJECT.md v1 scope includes the global blacklist; TDD 4.6 gives it an admin `/blacklist`
endpoint. Feed auto-population is M4 (D-SRL-4).

**Acceptance Criteria**:

1. WHEN an `admin` adds a global blacklist entry (arbitrary IPv4 `source_cidr`, `scope=global`, `service_id=NULL`, `source=manual`) THEN the system SHALL persist it and audit it. `(SRL-28)`
2. WHEN a `tenant_user` calls any global-blacklist endpoint THEN the system SHALL reject with 403 (`require_admin`), no side effect. `(SRL-29)`
3. WHEN an `admin` lists/views/deletes global blacklist entries THEN the system SHALL return/remove them; feed-sourced entries (`source=feed`, added by M4) SHALL be distinguishable by `source` — no feed rows exist in this feature. `(SRL-30)` *(D-SRL-4)*

**Independent Test**: Admin adds global blacklist `185.0.0.0/8` → 200 + audit; tenant_user POST /blacklist → 403; admin lists → sees the manual entry with `source=manual`.

---

### P1: Tenant isolation & CIDR-scope enforcement (wires AUTH-14, closes TCA-16 hook) ⭐ MVP

**User Story**: As the system owner, I want every service/rule/list write to enforce tenant ownership and
destination-CIDR scope using the shared primitives, so isolation is identical across resources and CIDR
revocation is safe.

**Why P1**: PRD 5.2/12.1; this feature is where AUTH-14 becomes concrete and where tenant-cidr's `TCA-16`
stub becomes real.

**Acceptance Criteria**:

1. WHEN a `tenant_user` attempts to read or modify another tenant's service, rule, whitelist, or blacklist entry THEN the system SHALL deny fail-closed (404/403) with **zero** bytes of the other tenant's data. `(SRL-31)` *(AUTH-12/13)*
2. WHEN an `admin` attempts to revoke an `AllocatedCIDR` that still contains any active `ProtectedService` (or its list entries) THEN the system SHALL refuse with 409 and name the blocking service(s) — realizing tenant-cidr's dependency-count hook (`TCA-16`). `(SRL-32)`
3. WHEN any destination-scope decision is made THEN the system SHALL call the reusable `cidr_in_tenant_allocation` / `require_within_allocation` primitive unchanged (import, do not reimplement). `(SRL-33)`

**Independent Test**: acme user GET/PUT beta's service id → 404; allocate+create service, then revoke that CIDR → 409 naming the service; remove/disable+delete the service → revoke succeeds (tenant-cidr TCA-15/17).

---

### P1: Audit coverage for service/rule/list mutations ⭐ MVP

**User Story**: As an `admin`, I want every service/rule/list change recorded, so config custody is fully
traceable (PRD 11.2).

**Why P1**: Mandated by PRD 11.2 / AUTH-25; reuses auth-rbac's audit writer.

**Acceptance Criteria**:

1. WHEN any service, plan, allow-rule, whitelist, or blacklist entry is created/edited/deleted THEN the system SHALL write exactly one credential-free audit event (actor, action, target_type/id, outcome, timestamp) via the shared `audit.record_event`, in the same transaction. `(SRL-34)`
2. WHEN a dangerous action (disable service, delete service) is attempted — success or refusal — THEN the system SHALL audit it with the resulting outcome. `(SRL-35)`

**Independent Test**: Run one of each mutation + one refused delete; audit log shows one well-formed row per attempt with correct `outcome` and no credential material.

---

### P2: ServicePlan oversubscription warning

**User Story**: As an `admin`, I want a warning when committed bandwidth is oversubscribed, so I notice
before the node is over-committed.

**Why P2**: PRD 7.2 line 285 warning; advisory, non-blocking — not required to unblock service CRUD.

**Acceptance Criteria**:

1. WHEN an `admin` sets `committed_clean_gbps` such that `Σ committed_clean_gbps` over all active services would exceed `node_clean_capacity` THEN the system SHALL still persist it but return a non-blocking oversubscription **warning** (with current sum vs capacity). `(SRL-36)` *(A-SRL-4)*

**Independent Test**: With capacity 40 and existing committed 39, set a service committed=5 → 200 + warning "44 > 40".

---

### P2: Allow-rule overlap dry-run

**User Story**: As a service owner, I want to pre-check a candidate rule for overlap, so I can choose
priorities without trial-and-error.

**Why P2**: UX nicety mirroring tenant-cidr's overlap-check; the create path already warns (SRL-18).

**Acceptance Criteria**:

1. WHEN a caller requests an overlap check for a candidate rule on a service THEN the system SHALL return which existing rule(s) it would overlap, mutating nothing (read-only dry-run). `(SRL-37)`

**Independent Test**: Dry-run a candidate overlapping rule-10 → reports rule-10; a disjoint candidate → reports clear; neither writes a row.

---

## Edge Cases

- WHEN two requests race to create rules with the same `priority` on one service THEN at most one SHALL succeed (unique constraint on (`service_id`,`priority`)). `(SRL-38)`
- WHEN a zero-rule service in `allow-rule-only` mode is enabled THEN all non-whitelisted traffic to it is dropped (`not_allowed`) — a deny-all-except-whitelist posture, which is expected, not an error. `(SRL-39)`
- WHEN two requests race to create services with overlapping destination `cidr_or_ip` THEN at most one SHALL succeed (DB exclusion constraint arbitrates; loser gets 409). `(SRL-40)`
- WHEN a source is present in **both** a service's whitelist and its blacklist THEN the system SHALL persist both without a hard conflict; runtime precedence (whitelist evaluated before blacklist per the M2/M3 pipeline) governs the verdict — documented, not blocked. `(SRL-41)`
- WHEN a disabled service still has whitelist/VIP entries THEN disable's drop-all SHALL override them (no bypass while disabled — AD-002); the entries are retained for re-enable. `(SRL-42)`
- WHEN `committed_clean_gbps == ceiling_clean_gbps` (or `committed == 0`) THEN the system SHALL accept it (`committed ≤ ceiling`; 0 = best-effort only). `(SRL-43)`
- WHEN a service's `cidr_or_ip` is edited to a range now overlapping another active service THEN the system SHALL reject with 409 (same rule as create — SRL-04). `(SRL-44)`

---

## Requirement Traceability

| Requirement ID | Story | PRD/AD ref | Phase | Status |
| --- | --- | --- | --- | --- |
| SRL-01..08 | P1: Service + Plan CRUD (scoped, non-overlap) | 6.3, 7.2, AUTH-14, D-SRL-3 | - | Pending |
| SRL-09..11 | P1: Enable/disable (drop-all, confirm+audit) | 6.3, 11.2, AD-002 | - | Pending |
| SRL-12..14 | P1: Delete service (disable-first, cascade) | 11.2, D-SRL-2 | - | Pending |
| SRL-15..21 | P1: AllowRule CRUD (≤16, unique priority, warn) | 6.4, AD-004 | - | Pending |
| SRL-22..25 | P1: Whitelist/VIP CRUD (scoped, arbitrary src) | 6.5, AD-003, D-SRL-1 | - | Pending |
| SRL-26..27 | P1: Service blacklist CRUD | 6.5, D-SRL-1 | - | Pending |
| SRL-28..30 | P1: Global blacklist admin CRUD | 6.5, D-SRL-4 | - | Pending |
| SRL-31..33 | P1: Isolation & CIDR-scope (AUTH-14, TCA-16) | 5.2, 12.1, 7.2 | - | Pending |
| SRL-34..35 | P1: Audit coverage | 11.2, AUTH-25 | - | Pending |
| SRL-36 | P2: Oversubscription warning | 7.2 | - | Pending |
| SRL-37 | P2: Allow-rule overlap dry-run | 6.4 | - | Pending |
| SRL-38..44 | Edge cases | 6.3, 6.4, 6.5, AD-002/003/004 | - | Pending |

**ID format:** `SRL-[NUMBER]`
**Status values:** Pending → In Design → In Tasks → Implementing → Verified
**Coverage:** 44 requirements total, 0 mapped to tasks yet (Tasks phase pending) ⚠️

**Cross-feature:** wires auth-rbac `AUTH-14` (CIDR-scope on writes) with a concrete resource; realizes
tenant-cidr `TCA-16` (revoke-in-use blocked) via its dependency-count hook; consumes the
`cidr_in_tenant_allocation` primitive, `AllocatedCIDR`, `core/cidr`, `require_admin`, ownership guard,
and `audit.record_event` unchanged.

---

## Success Criteria

- [ ] A service can only be created on a destination fully within the tenant's active `AllocatedCIDR`,
      proven by an isolation/scope test using the shared primitive (outside → 403; inside → 201).
- [ ] No two active services overlap on destination — proven by an exclusion-constraint integration test
      (concurrent overlapping creates → exactly one succeeds).
- [ ] A `tenant_user` cannot read or modify another tenant's service/rule/list — 404/403 with **zero**
      bytes leaked (isolation test pair).
- [ ] `AllowRule` invariants hold under the DB: `priority` unique per service, ≤16 rules, invalid
      port-range/protocol rejected; overlapping rules are warned, not blocked.
- [ ] Whitelist/blacklist accept arbitrary external IPv4 sources and reject IPv6/malformed; global
      blacklist is admin-only.
- [ ] Disabling a service is drop-all + confirm + audited; deleting requires disabled-first and cascades
      children; both audited on success and refusal.
- [ ] Revoking an `AllocatedCIDR` that contains an active service is refused (closes TCA-16); after the
      service is deleted, revoke succeeds.
- [ ] Every service/rule/list mutation produces exactly one credential-free audit row with the correct
      `outcome`.

---

## Decisions & Assumptions (flagged for confirmation)

See `context.md` for full rationale.
1. **D-SRL-1** — whitelist/blacklist sources are **arbitrary IPv4** (external allowed); only the service's
   destination `cidr_or_ip` is scoped to `AllocatedCIDR`.
2. **D-SRL-2** — delete service = **disable-first, then cascade** children; dangerous + audited.
3. **D-SRL-3** — service destination `cidr_or_ip` **must not overlap** another active service (global GiST
   exclusion, mirrors AllocatedCIDR).
4. **D-SRL-4** — **manual** global blacklist CRUD ships here; **feed** population is M4 (`source` field
   discriminates).
5. **A-SRL-1** *(confirmed 2026-07-07)* — plan `committed`/`ceiling` are **admin-only** (commercial
   commitment); tenant self-service is not in v1.
6. **A-SRL-2** — `mode` = `allow-rule-only` only in v1. **A-SRL-3** *(confirmed 2026-07-07)* — this
   feature stops at `apply_status=pending` **and owns the `version` bump** on every mutation; the
   `queued→applying→active→failed` state machine + Redis enqueue is the separate Apply-status feature.
   **A-SRL-4** — `node_clean_capacity` is a node config value. **A-SRL-6** — no `expires_at` on lists in v1.
