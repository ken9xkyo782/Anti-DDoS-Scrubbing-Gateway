# Service, Rule & List Management — Context (Discuss output)

**Spec:** `.specs/features/service-rule-list/spec.md`
**Captured:** 2026-07-07 (discuss within Specify)
**Status:** Ready for design

---

## Feature Boundary

Control-plane (FastAPI + Postgres) **CRUD** for the per-service protection config a tenant owns:
`ProtectedService` + its 1:1 `ServicePlan`, `AllowRule`, service-scoped whitelist/VIP, service/tenant
blacklist, and admin-managed **manual** global blacklist entries. Every write is tenant-scoped
(fail-closed), destination-scoped to the tenant's `AllocatedCIDR` (AUTH-14 via the reusable
`cidr_in_tenant_allocation` primitive), and audited. This feature **persists and validates** config
only — it does not build BPF maps (M2/M3), run the apply-status state machine, or enqueue Redis jobs
(the separate *Apply-status state machine* feature does). Parallels tenant-cidr: pure control-plane
persistence + scoping + audit.

---

## Implementation Decisions

Four gray areas the PRD/TDD did not settle were resolved with the user before writing the spec. Each
shapes acceptance criteria and (where noted) a Postgres constraint.

### D-SRL-1: Whitelist/blacklist source CIDRs are arbitrary IPv4 (external allowed)

**Question:** The roadmap says whitelist/blacklist are "all CIDR-scoped." Does that mean the *source*
IP in a whitelist/blacklist entry must be within the tenant's `AllocatedCIDR`?
**Decision:** **No.** Only the service's own **destination** `cidr_or_ip` is constrained to the
tenant's `AllocatedCIDR` (AUTH-14 / PRD 7.2). Whitelist/blacklist entries match a **source** IP, which
is arbitrary IPv4 — an external trusted partner/upstream (whitelist) or an external attacker range
(blacklist). "Scoped" for lists means **attached to a `service_id`** (per AD-003), not source-in-allocation.
Only IPv6/malformed sources are rejected (422).
**Why:** Whitelisting/blacklisting external sources is the entire point; requiring the source ⊆
allocation would forbid the normal case. AD-003 already defines the list key as `service_id`+source CIDR.
**Impact:** `SRL-22..27`; whitelist/blacklist source validation = IPv4 canonical only (reuse
`core/cidr` for IPv6/malformed rejection, **not** the allocation-containment check). The
allocation-containment check applies **only** to `ProtectedService.cidr_or_ip`.

### D-SRL-2: Delete a service = disable-first, then cascade its children (dangerous + audited)

**Question:** Deleting a `ProtectedService` that still owns `AllowRule`s and whitelist/blacklist
entries — cascade, block, or disable-first?
**Decision:** **Disable-first, then cascade.** Delete is **refused (409)** while the service is
`enabled` ("disable it first"). Disabling is an intentional protection cut requiring **confirm + audit**
(AD-002). Once disabled, delete **hard-deletes the service and cascades** its own child `AllowRule`s +
whitelist + service-blacklist entries (they are composed by the service). Delete is a **dangerous**
action, audited on both success and refusal.
**Why:** Deleting a *live* service is a bigger protection cut than disabling one; forcing the explicit
disable→delete sequence makes each step auditable and prevents a one-call live cut. Children are owned
by the service, so cascade (not block) is the natural teardown — unlike the CIDR↔service *scoping*
relationship, which blocks (D-TCA-2).
**Impact:** `SRL-09..14`. FK `allow_rule/whitelist/blacklist → protected_service` is `ON DELETE CASCADE`;
service delete pre-checks `enabled=false`. Cascade removal is itself covered by the single delete audit
event (children are not individually re-audited).

### D-SRL-3: Service destination `cidr_or_ip` must not overlap another active service (global)

**Question:** May two `ProtectedService`s cover overlapping destination ranges (e.g. a /25 inside a /24)?
**Decision:** **No overlap.** A service's active destination `cidr_or_ip` must not overlap any other
**active** service's destination — enforced **globally** at the DB layer (partial GiST exclusion,
mirroring `AllocatedCIDR`). One destination IP maps to **exactly one** service.
**Why:** Guarantees a deterministic `service_map` lookup in the data-plane and unambiguous "which
service owns this IP" in the UI/audit. Mirrors the AllocatedCIDR non-overlap model the team already
adopted (D-TCA-1) — one DB constraint, race-proof.
**Impact:** `SRL-04`, `SRL-40`. New `protected_service_active_dest_no_overlap` partial GiST exclusion
`EXCLUDE USING gist (cidr_or_ip inet_ops WITH &&) WHERE (enabled = true ...)` — exact predicate
(active/enabled vs deleted) finalized in Design. Disabled/deleted services free the space.

### D-SRL-4: Manual global blacklist CRUD is in this feature; feed population is M4

**Question:** The global blacklist (all-services, admin-managed) vs the scheduled threat-feed that
auto-populates it (M4) — what ships here?
**Decision:** **Manual admin CRUD of global blacklist entries ships here** (matches TDD 4.6's admin
`/blacklist` endpoint). The **scheduled threat-feed sync** that auto-populates the global blacklist is
**deferred to M4** (*Threat intelligence feed sync*). A `source` discriminator (`manual` | `feed`)
distinguishes them so M4 adds feed rows without schema churn.
**Why:** Manual global-deny is a plain control-plane list operation belonging with "list management";
the feed adds fetch/validate/normalize/dedup machinery that is its own M4 feature.
**Impact:** `SRL-28..30`. Global blacklist entries are admin-only (`require_admin`), arbitrary IPv4
source, `service_id = NULL`, `scope = global`, `source = manual`. M4 later inserts `source = feed` rows
and wires the "whitelist overlaps a feed IP → alert+audit" rule (AD-003), which cannot fire in this
feature because no feed exists yet.

---

## Flagged assumptions (written into spec, confirm during Design)

- **A-SRL-1 — Plan sizing (`committed_clean_gbps` / `ceiling_clean_gbps`) is admin-only.** *(CONFIRMED
  2026-07-07.)* These are a commercial/SLA commitment bounded by node capacity (`Σ committed ≤
  node_clean_capacity`, PRD 7.2), so a `tenant_user` may **view** their plan and manage service config
  (rules/lists/enable/disable) but **not** set or raise committed/ceiling (→ 403). A tenant-created
  service gets a default plan (committed 0 / configured default) that admin later sizes. Tenant
  self-service is not in v1. (`SRL-07`)
- **A-SRL-2 — `mode` field carried from TDD 4.6 example with the single v1 value `allow-rule-only`.**
  Other service modes are out of v1 scope; the field exists for forward-compat. Confirm the value set. (`SRL-01`)
- **A-SRL-3 — Apply-status boundary.** *(CONFIRMED 2026-07-07.)* Every mutation persists `apply_status =
  pending` and **this feature owns the `version` bump** (monotonic per service, incremented on every
  service/plan/rule/list mutation). The `pending → queued → applying → active → failed` state machine and
  Redis enqueue are the separate *Apply-status state machine* feature (M1); this feature stops at
  `pending`. (`SRL-01`, `SRL-06`)
- **A-SRL-4 — `node_clean_capacity` is a node-level config value** read for the oversubscription warning
  (`SRL-36`); its exact source (settings/env/DB) is a Design detail. IPv6/host-bits/`0.0.0.0/0` handling
  for `cidr_or_ip` reuses tenant-cidr's `core/cidr` rules.
- **A-SRL-5 — Category ID:** `SRL` (Service, Rule & List). Feature directory `service-rule-list`.
- **A-SRL-6 — No `expires_at` on whitelist/blacklist in v1** (BL-07 reconciliation sweep is GA/deferred).

---

## Specific References

- **AD-002** (service disable = drop-all + confirm + audit), **AD-003** (whitelist scoped by `service_id`,
  no cross-service bypass; feed-overlap → alert+audit), **AD-004** (allow-rule first-match by ascending
  `priority`, terminal, no fall-through — so overlapping rules are **warned, not blocked**).
- **TDD 4.6** endpoint sketch (`/services`, `/services/{id}/rules`, `/services/{id}/whitelist`,
  `/blacklist`) and 4.7 data-model constraints (`committed ≤ ceiling`; `priority` unique per service;
  ≤16 rules; IPv6 rejected).

---

## Deferred Ideas

- Threat-feed auto-population of the global blacklist + whitelist-overlaps-feed alert (M4).
- `expires_at` reconciliation sweep for whitelist/blacklist entries (BL-07, GA).
- Tenant self-service plan sizing within an admin-set cap (relaxation of A-SRL-1).
- Additional service `mode`s beyond `allow-rule-only`.
