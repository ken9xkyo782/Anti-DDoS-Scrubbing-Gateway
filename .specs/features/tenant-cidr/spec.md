# Tenant & CIDR Allocation Specification

**Milestone:** M1 — Control-plane foundation & tenant model
**Category ID:** TCA
**Status:** Design complete (`design.md`); awaiting confirmation → Tasks
**Depends on:** Auth & RBAC (`.specs/features/auth-rbac/`) — consumes `require_admin`, the tenant-ownership guard, and the audit writer; **resolves** its deferred `AUTH-36`.
**Discuss context:** `.specs/features/tenant-cidr/context.md` (D-TCA-1..3)

## Problem Statement

Every downstream control-plane resource — services, allow-rules, whitelist/blacklist entries, telemetry, chargeback — is scoped to a tenant and confined to the IP space that tenant is entitled to (PRD 5.2, 7.2). Auth & RBAC created only a *minimal* `Tenant` stub (id/name/status) so `User.tenant_id` could resolve; it deliberately deferred tenant lifecycle and all of CIDR allocation to this feature. This feature delivers full tenant lifecycle plus the `AllocatedCIDR` model with a hard non-overlap guarantee, and — mirroring auth-rbac's reusable guard/audit primitives — it establishes the **CIDR-scope primitive** (`is target ⊆ this tenant's active allocations?`) that Service/Whitelist/Blacklist features call to satisfy `AUTH-14` and PRD 12.1.

## Goals

- [ ] Full admin tenant lifecycle: create, list, view, edit, suspend/reactivate, delete — every mutation audited (PRD 6.1, 11.2).
- [ ] `AllocatedCIDR` model with a **global non-overlap** guarantee across active allocations, enforced at the DB layer (Postgres GiST exclusion), IPv4-only (PRD 7.2, D-TCA-1).
- [ ] Admin allocate / revoke ranges, plus usage-status and overlap-check (dry-run) views (PRD 6.1).
- [ ] Fail-closed lifecycle rules: revoke-in-use **blocked** (D-TCA-2); delete-tenant-with-dependents **blocked** (D-TCA-3, closes `AUTH-36`).
- [ ] A reusable **CIDR-scope primitive** — `target ⊆ tenant's active allocations` — that later features import unchanged to enforce `AUTH-14` / PRD 5.2 / 12.1.
- [ ] Read-only self-view of allocations for `tenant_user` (dashboard "your ranges"), strictly tenant-isolated.

## Out of Scope

Explicitly excluded to prevent scope creep.

| Feature | Reason |
| --- | --- |
| `ProtectedService` / `ServicePlan` CRUD and `committed_clean_gbps` oversubscription warning (PRD 7.2 line 285) | Separate M1 feature; this spec only exposes the CIDR-scope hook it will call |
| Whitelist / blacklist CRUD | Separate M1 feature; consumes the scope primitive here |
| IPv6 CIDR allocation | v1 hard-drops IPv6 (PRD 4.2/7.2); allocations are IPv4-only |
| IPAM niceties — subnet calculator, next-free-range suggestion, auto-split | Not required by PRD; manual admin allocation only |
| Per-tenant allocation quotas / limits | Not in PRD scope for v1 |
| Data-plane propagation of allocation changes | Allocations are not data-plane objects; only *services* built within them reach the data-plane (M2+) |
| MFA/SSO, admin session mechanics | Owned by Auth & RBAC |

---

## User Stories

### P1: Tenant lifecycle management (admin) ⭐ MVP

**User Story**: As an `admin`, I want to create and manage tenants, so that I can onboard paying units and control their status before allocating them any IP space.

**Why P1**: Nothing can be allocated or scoped without a tenant record; PRD 6.1 requires it and it upgrades auth-rbac's stub to a first-class managed entity.

**Acceptance Criteria**:

1. WHEN an `admin` creates a tenant with a unique `name` THEN the system SHALL persist it with `status=active`, `created_at/updated_at`, and record an audit event. `(TCA-01)`
2. WHEN an `admin` creates a tenant whose `name` collides with an existing one THEN the system SHALL reject with a uniqueness error and create nothing. `(TCA-02)`
3. WHEN an `admin` edits a tenant's `name` or `status` THEN the system SHALL apply the change, bump `updated_at`, and audit it. `(TCA-03)`
4. WHEN an `admin` suspends a tenant (`status=suspended`) THEN the system SHALL cause that tenant's users to fail authentication/authorization on their next request (via `AUTH-34`/fresh-user check) WITHOUT revoking its allocations, and audit it. `(TCA-04)`
5. WHEN an `admin` lists or views tenants THEN the system SHALL return each tenant with `status`, active-allocation count, and user count. `(TCA-05)`
6. WHEN a `tenant_user` calls any tenant-management endpoint THEN the system SHALL reject with 403 and perform no side effect (`require_admin`). `(TCA-06)`

**Independent Test**: Admin creates tenant "acme" (audited), duplicate name → 409; edit status→suspended and a pre-existing acme user's next request → 401; list shows acme with 0 allocations; tenant_user hitting the endpoint → 403.

---

### P1: Delete tenant — dependent-safe (resolves AUTH-36) ⭐ MVP

**User Story**: As an `admin`, I want deleting a tenant to be safe against orphaning its users and IP space, so that a delete can never leave active users or dangling allocations behind.

**Why P1**: Auth & RBAC (`AUTH-36`) explicitly deferred this rule here; PRD 11.2 lists "delete tenant" as a dangerous, audit-required action.

**Acceptance Criteria**:

1. WHEN an `admin` deletes a tenant that still has any user (active or disabled) OR any non-revoked `AllocatedCIDR` THEN the system SHALL refuse with 409, name the blocking dependents, and change nothing. `(TCA-07)`
2. WHEN an `admin` deletes a tenant with no users and no active allocations THEN the system SHALL hard-delete it and record an audit event. `(TCA-08)`
3. WHEN a tenant deletion is refused or performed THEN the outcome (`denied`/`success`) SHALL be audited as a dangerous admin action. `(TCA-09)`

**Independent Test**: Create tenant + one user → delete → 409 listing the user; remove the user and revoke its CIDRs → delete → 204 + one audit row.

---

### P1: CIDR allocation with global non-overlap (admin) ⭐ MVP

**User Story**: As an `admin`, I want to allocate IPv4 CIDR ranges to a tenant with a guarantee that no two ranges overlap, so that each protected address belongs to exactly one tenant.

**Why P1**: The core of the feature (PRD 6.1, 7.2) and the precondition for every service a tenant can create (PRD 5.2, 6.3).

**Acceptance Criteria**:

1. WHEN an `admin` allocates a valid canonical IPv4 CIDR to an `active` tenant AND it overlaps no existing **active** allocation THEN the system SHALL persist an `AllocatedCIDR` with `status=active`, `allocated_by=<admin id>`, `created_at`, and audit it. `(TCA-10)`
2. WHEN an `admin` allocates a CIDR that overlaps ANY existing active allocation (same or different tenant — global rule, D-TCA-1) THEN the system SHALL reject with 409, identify the conflicting range, and write nothing. `(TCA-11)`
3. WHEN an `admin` submits an IPv6 CIDR or a malformed value THEN the system SHALL reject with 422 (IPv4-only, v1). `(TCA-12)`
4. WHEN an `admin` submits a non-canonical CIDR with host bits set (e.g. `10.0.0.5/24`) THEN the system SHALL reject with 422 and name the canonical network form (`10.0.0.0/24`). `(TCA-13)` *(assumption A1)*
5. WHEN an `admin` allocates to a `suspended` or non-existent tenant THEN the system SHALL reject and write nothing. `(TCA-14)`

**Independent Test**: Allocate `203.0.113.0/24` to acme (audited); allocate overlapping `203.0.113.128/25` to beta → 409 naming acme's range; allocate `2001:db8::/32` → 422; allocate `10.0.0.5/24` → 422 suggesting `10.0.0.0/24`.

---

### P1: CIDR revoke — in-use blocked (admin) ⭐ MVP

**User Story**: As an `admin`, I want to revoke a CIDR only when nothing depends on it, so that revocation never silently strips protection from live resources.

**Why P1**: PRD 6.1 (revoke); D-TCA-2 makes it fail-closed; frees address space for re-allocation.

**Acceptance Criteria**:

1. WHEN an `admin` revokes an active `AllocatedCIDR` that has NO dependent resources THEN the system SHALL set `status=revoked`, audit it, and free the range so an equal or overlapping CIDR can be re-allocated. `(TCA-15)`
2. WHEN an `admin` revokes an `AllocatedCIDR` that still contains any `ProtectedService` or whitelist/blacklist entry THEN the system SHALL reject with 409, list the blocking dependents, and change nothing (D-TCA-2). `(TCA-16)`
3. WHEN a range is revoked THEN it SHALL no longer count toward the non-overlap constraint, and re-allocating the same range SHALL succeed. `(TCA-17)`

**Independent Test**: Revoke an empty range → 200, `status=revoked`, re-allocate same range → 200. (Dependent-block path verified with a stub/count when the Service feature ships — the rule + hook exist here.)

---

### P1: Usage status & overlap check views (admin) ⭐ MVP

**User Story**: As an `admin`, I want to see how allocations are used and pre-check a candidate range for overlap, so that I can plan allocations without trial-and-error 409s.

**Why P1**: Explicitly required by PRD 6.1 ("xem trạng thái sử dụng, kiểm tra overlap").

**Acceptance Criteria**:

1. WHEN an `admin` views a tenant's allocations THEN the system SHALL return each CIDR with `status`, `allocated_by`, `created_at`, and a usage summary (count of resources within it; 0 until dependent features ship). `(TCA-18)`
2. WHEN an `admin` requests an overlap check for a candidate CIDR THEN the system SHALL return whether it overlaps any active allocation and, if so, which range(s) — a read-only dry-run of the allocation constraint, mutating nothing. `(TCA-19)`

**Independent Test**: Overlap-check `203.0.113.64/26` against the existing `203.0.113.0/24` → reports conflict; check a disjoint range → reports clear; neither writes a row.

---

### P1: CIDR-scope primitive (reusable) ⭐ MVP

**User Story**: As a developer of later features, I want one authoritative "is this IP/CIDR inside this tenant's allocations?" check, so that services, whitelist, and blacklist all enforce `AUTH-14` identically and fail closed.

**Why P1**: This is the feature's reusable contribution (parallel to auth-rbac's guard); PRD 5.2/12.1 and `AUTH-14` depend on it.

**Acceptance Criteria**:

1. WHEN a caller asks whether a target IP/CIDR is within a tenant's allocations THEN the system SHALL expose a reusable primitive returning true ONLY IF the target is fully contained within some single **active** `AllocatedCIDR` owned by that tenant, else false. `(TCA-20)`
2. WHEN the target is only partially covered, spans multiple allocations, or matches a `revoked` allocation THEN the primitive SHALL return not-contained (deny). `(TCA-21)`
3. WHEN the primitive is invoked with a missing/unknown tenant or malformed target THEN it SHALL fail closed (deny), consistent with `AUTH-10`. `(TCA-22)`

**Independent Test**: With acme owning `203.0.113.0/24`: `203.0.113.10/32` ⊆ → true; `203.0.113.0/23` (superset) → false; a revoked range → false; unknown tenant → false.

---

### P2: Tenant self-view of allocations (tenant_user)

**User Story**: As a `tenant_user`, I want to see the CIDR ranges allocated to my tenant (read-only), so that I know where I may create services.

**Why P2**: Improves the tenant dashboard (PRD 6.2) but not required to unblock admin allocation or downstream features.

**Acceptance Criteria**:

1. WHEN a `tenant_user` requests their allocations THEN the system SHALL return only their own tenant's active `AllocatedCIDR`s, read-only. `(TCA-23)`
2. WHEN a `tenant_user` attempts to read or modify another tenant's allocations THEN the system SHALL return 404/403 with zero bytes of the other tenant's data (PRD 5.2/12.1). `(TCA-24)`

**Independent Test**: acme user lists → sees only acme ranges; requests beta's allocation id → 404; any write verb → 403.

---

### P1: Audit coverage for tenant/CIDR mutations ⭐ MVP

**User Story**: As an `admin`, I want every tenant and allocation change recorded, so that IP-space custody is fully traceable (PRD 11.2).

**Why P1**: Mandated by PRD 11.2; reuses auth-rbac's audit writer.

**Acceptance Criteria**:

1. WHEN a tenant is created/edited/suspended/deleted, or a CIDR is allocated/revoked THEN the system SHALL write exactly one audit event (actor, action, target_type/id, outcome, timestamp) via the shared `audit.record_event`. `(TCA-25)`
2. WHEN a dangerous action (delete tenant, revoke CIDR) is attempted — whether it succeeds or is refused — THEN the system SHALL audit it with the resulting outcome. `(TCA-26)`

**Independent Test**: Run each mutation + one refused delete and one refused revoke; audit log shows one well-formed row per attempt with correct `outcome`.

---

## Edge Cases

- WHEN two requests race to allocate overlapping ranges THEN at most one SHALL succeed (DB exclusion constraint arbitrates; the loser gets 409). `(TCA-27)`
- WHEN an `admin` allocates `0.0.0.0/0` or another whole-space/reserved range THEN the system SHALL reject it (would block all future allocations). `(TCA-28)` *(assumption A2)*
- WHEN an `admin` revokes an already-`revoked` allocation THEN the system SHALL treat it as a no-op/409 gracefully, never double-audit a state change. `(TCA-29)`
- WHEN a `/32` single-host CIDR is allocated THEN the system SHALL accept it as a valid IPv4 allocation. `(TCA-30)`
- WHEN a suspended tenant is reactivated (`status=active`) THEN its previously-kept allocations SHALL remain valid and its users able to authenticate again. `(TCA-31)`
- WHEN an allocation is attempted while the owning tenant is being deleted (race) THEN exactly one of {delete, allocate} SHALL win and the result SHALL leave no active CIDR under a deleted tenant. `(TCA-32)`

---

## Requirement Traceability

| Requirement ID | Story | PRD ref | Phase | Status |
| --- | --- | --- | --- | --- |
| TCA-01..06 | P1: Tenant lifecycle management | 6.1, 7.1, 11.2 | Design | Pending |
| TCA-07..09 | P1: Delete tenant (resolves AUTH-36) | 11.2 | Design | Pending |
| TCA-10..14 | P1: CIDR allocation + global non-overlap | 6.1, 7.2 | Design | Pending |
| TCA-15..17 | P1: CIDR revoke (in-use blocked) | 6.1, 7.2 | Design | Pending |
| TCA-18..19 | P1: Usage & overlap-check views | 6.1 | Design | Pending |
| TCA-20..22 | P1: CIDR-scope primitive (reusable) | 5.2, 7.2, 12.1 (AUTH-14) | Design | Pending |
| TCA-23..24 | P2: Tenant self-view of allocations | 6.2, 5.2 | - | Pending |
| TCA-25..26 | P1: Audit coverage | 11.2 | Design | Pending |
| TCA-27..32 | Edge cases | 6.1, 7.2 | Design | Pending |

**ID format:** `TCA-[NUMBER]`
**Status values:** Pending → In Design → In Tasks → Implementing → Verified
**Coverage:** 32 requirements total, 0 mapped to tasks yet (Design pending) ⚠️

**Cross-feature:** resolves auth-rbac `AUTH-36` (delete-tenant rule) and provides the data + primitive behind `AUTH-14` (CIDR-scope enforcement).

---

## Success Criteria

- [ ] No two `active` allocations overlap — proven by an exclusion-constraint integration test (concurrent overlapping inserts → exactly one succeeds).
- [ ] A `tenant_user` cannot read or modify another tenant's allocations — 404/403 with **zero** bytes leaked (isolation test pair).
- [ ] Deleting a tenant with any user or active CIDR is refused; deleting an emptied tenant succeeds — both audited (closes AUTH-36).
- [ ] Revoking a range with a dependent resource is refused; revoking an empty range frees it for immediate re-allocation.
- [ ] The CIDR-scope primitive returns true only for fully-contained targets under active allocations and fails closed on unknown tenant / partial coverage / revoked range.
- [ ] Every tenant/CIDR mutation produces exactly one credential-free audit row with the correct `outcome`.
- [ ] The scope primitive and `AllocatedCIDR` model are imported unchanged by a second feature (Service CRUD) to enforce `ProtectedService.cidr_or_ip ⊆ AllocatedCIDR` (PRD 7.2).

---

## Decisions & Assumptions (flagged for confirmation)

1. **Global non-overlap (D-TCA-1).** Enforced as a Postgres GiST exclusion constraint partial on `status='active'`; revoked rows free the space. Stricter superset of PRD 7.2's cross-tenant wording.
2. **Revoke-in-use blocked (D-TCA-2).** Rule + dependency-count hook live here; the cross-table dependency check is fully wired when Service/Whitelist/Blacklist ship (their rows reference the allocation).
3. **Delete-tenant blocked until emptied (D-TCA-3).** Resolves `AUTH-36`; **suspend** is the reversible off-switch (keeps CIDRs, blocks logins).
4. **A1 — non-canonical CIDR rejected (422)** via native Postgres `cidr` type (rejects host bits), matching PROJECT.md's "native inet/cidr." Alternative (silent normalize to network address) is possible — confirm in Design.
5. **A2 — `0.0.0.0/0` and reserved/whole-space allocations rejected.** Confirm the exact reserved-range policy in Design (minimal: reject `/0`).
6. **`AllocatedCIDR.status` values:** `active | revoked` for v1 (soft-revoke retains custody history). No `pending` state — allocation is immediate.
