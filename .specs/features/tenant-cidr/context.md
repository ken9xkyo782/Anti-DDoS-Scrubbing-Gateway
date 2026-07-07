# Tenant & CIDR Allocation — Context (Discuss output)

**Spec:** `.specs/features/tenant-cidr/spec.md`
**Captured:** 2026-07-07 (discuss within Specify)

Three gray areas not decided by the PRD were resolved with the user before writing the spec. Each shapes acceptance criteria and one Postgres constraint.

## D-TCA-1: CIDR non-overlap is enforced GLOBALLY

**Question:** PRD 7.2 says `AllocatedCIDR` must not overlap "between different tenants." Enforce globally or literally cross-tenant only?
**Decision:** **Global no-overlap** — no two *active* allocations may overlap at all, even within the same tenant.
**Why:** Implementable as a single Postgres GiST exclusion constraint on `cidr` (partial: `WHERE status='active'`); downstream CIDR-scope checks stay unambiguous (a tenant holds a set of disjoint ranges). Strict superset of the PRD rule — still satisfies "no cross-tenant overlap."
**Impact:** `TCA-10`, `TCA-16`, `TCA-18`; exclusion constraint in Design. Revoked rows (`status='revoked'`) are excluded from the constraint so an equal/overlapping range can be re-allocated.

## D-TCA-2: Revoke a CIDR still in use is BLOCKED (fail-closed)

**Question:** Revoking an `AllocatedCIDR` that still contains `ProtectedService`/whitelist/blacklist entries — block, cascade, or soft-revoke-and-keep?
**Decision:** **Block until dependents removed** — revoke is refused (409) while any service or list entry sits inside the range; admin removes/relocates them first.
**Why:** Fail-closed, consistent with the product's security posture; no orphaned or silently-unprotected resources; no "revoked-but-still-serving" window.
**Impact:** `TCA-14`, `TCA-15`. Dependent resources (services, lists) ship in later M1 features, so this feature exposes the **rule + a dependency-count hook**; the actual cross-table check is wired when those features land. Empty-CIDR revoke = soft `status='revoked'`.

## D-TCA-3: Delete a tenant with users/CIDRs is BLOCKED (resolves AUTH-36)

**Question:** auth-rbac deferred the delete-tenant rule (AUTH-36) here. Deleting a tenant that still owns users and/or active CIDRs — block, cascade, or soft-delete?
**Decision:** **Block until emptied** — deletion refused (409) until the tenant's users are removed/reassigned and its CIDRs revoked; then hard-delete.
**Why:** No orphaned active `tenant_user`s (the exact AUTH-36 hazard); forces explicit, auditable cleanup; avoids one-click destruction of many resources.
**Impact:** `TCA-06`, `TCA-07`. Note: **suspend** (`status='suspended'`) is the reversible "turn a tenant off" path (blocks logins via AUTH-34) and does NOT free CIDRs — distinct from delete.

---

## Flagged assumptions (written into spec, confirm during Design)

- **A1 — Non-canonical CIDR (host bits set, e.g. `10.0.0.5/24`):** rejected (422) with the canonical network form named in the error, by using Postgres' native `cidr` type (rejects host bits). Aligns with PROJECT.md "native inet/cidr types." (`TCA-12`)
- **A2 — `0.0.0.0/0` / whole-space allocation:** rejected — allocating the entire space would block every future allocation and is almost certainly an error. (`TCA-28`)
- **A3 — Category ID:** `TCA` (Tenant & CIDR Allocation).
