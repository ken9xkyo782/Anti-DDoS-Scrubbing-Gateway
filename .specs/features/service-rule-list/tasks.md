# Service, Rule & List Management (API) Tasks

**Design**: `.specs/features/service-rule-list/design.md`
**Spec**: `.specs/features/service-rule-list/spec.md` (SRL-01..44)
**Context**: `.specs/features/service-rule-list/context.md` (D-SRL-1..4; A-SRL-1/3 confirmed)
**Testing**: `.specs/codebase/TESTING.md`
**Status**: Execute — T1–T3 complete, T4 next

**Cross-feature prerequisite:** requires **Auth & RBAC (T1–T12)** and **Tenant & CIDR allocation (T1–T7)** executed first. This feature reuses their `control-plane/` skeleton, `Base`/`User`/`Tenant`/`AuditEvent`/`AllocatedCIDR` models, `app/core/deps.py` guards (`require_admin`, `get_current_user`, `authorize_tenant_resource`, `scope_to_tenant`, `require_within_allocation`), `app/core/cidr.py`, `app/services/allocations.py::cidr_in_tenant_allocation`, and `app/services/audit.py`. Its Alembic revision's `down_revision` = **tenant-cidr's head**.

**Stack for all tasks:** async FastAPI (asyncpg + SQLAlchemy 2.0 `AsyncSession`, `redis.asyncio`), httpx `AsyncClient` tests, ruff + mypy. Integration tests need `compose.test.yml` up. Apply the `coding-guidelines` skill during implementation. Only unit-tested tasks may be `[P]` (AD-008 / TESTING.md — shared compose stack serializes integration).

---

## Execution Plan

### Phase 1 — Foundation (T1 parallel with T2)
```
T1 [P]   (unit — pure rule-match helpers, no DB)
T2       (integration — 5 tables + exclusion/unique/CHECK constraints + migration)
```

### Phase 2 — Service core (Sequential)
```
T2 ──► T3        (ProtectedService + ServicePlan service; version bump; services_in_cidr)
```

### Phase 3 — Dependent services & wiring (Sequential — shared compose stack)
```
T3 ──► T4        (rule service)         ── needs T1 too
T3 ──► T5        (list service)
T3 ──► T6        (wire TCA-16 into allocations.revoke)
T3 ──► T7        (deps: load_service_for_principal)
```

### Phase 4 — Routers (Sequential)
```
{T3,T7} ──► T8   (services router + plan/enable/disable)
{T4,T7} ──► T9   (rules router + overlap-check)
{T5,T7} ──► T10  (lists router: whitelist + service blacklist)
T5      ──► T11  (global blacklist router, admin)
```

**Dependency graph**
```
T1 [P] ─────────────────────────┐
                                 ▼
T2 ─► T3 ─┬─► T4 ──────────────► T9 ◄─ T7
          ├─► T5 ─┬───────────► T10 ◄─ T7
          │       └───────────► T11
          ├─► T6
          └─► T7 ─────────────► T8
```

---

## Task Breakdown

### T1: Rule-match helpers [P]
**What**: `app/core/rulematch.py` — pure helpers: `validate_port_range(lo, hi)` (raise `PortRangeError` unless `0≤lo≤hi≤65535`, or both `None`), `rules_overlap(a, b) -> bool` (protocols intersect — equal or either `any` — AND src-port ranges intersect AND dst-port ranges intersect), `find_overlaps(existing, candidate) -> list` (powers the create-time warning and the dry-run). Plus `PortRangeError` and a small `RuleView` dataclass.
**Where**: `control-plane/app/core/rulematch.py`, `control-plane/tests/unit/test_rulematch.py`
**Depends on**: None
**Reuses**: stdlib only; establishes the rule-overlap contract (design "Rule-match helper")
**Requirement**: SRL-18 (pure), SRL-19 (pure), SRL-37 (pure)
**Tools**: Bash, Write/Edit · Skill: `coding-guidelines`
**Done when**:
- [x] `validate_port_range` accepts `(80,80)`, `(1,65535)`, `(None,None)`; rejects `(80,79)`, `(-1,10)`, `(0,70000)` (SRL-19)
- [x] `rules_overlap` truth table: same-protocol touching/nested ranges → true; disjoint ports → false; `any` vs `tcp` → protocol-intersect true; `icmp` (ports None) equal → true, vs `udp` → false (SRL-18)
- [x] `find_overlaps` returns every overlapped rule for a candidate; empty list when disjoint (SRL-18/37)
- [x] Gate check passes: `ruff check . && ruff format --check . && mypy app/ && pytest -q -m unit` (quick)
- [x] Test count: ≥8 tests pass (no silent deletions)
**Tests**: unit
**Gate**: quick
**Commit**: `feat(rule): pure port-range validation & rule-overlap helpers`

---

### T2: Models, constraints & migration
**What**: Add to `app/db/models.py`: `ProtectedService`, `ServicePlan`, `AllowRule`, `WhitelistEntry`, `BlacklistEntry` + enums (`ApplyStatus`, `ServiceMode`, `Protocol`, `BlacklistScope`, `BlacklistSource`, `OveragePolicy`). Constraints: **`protected_service_dest_no_overlap`** = `ExcludeConstraint(("cidr_or_ip","&&"), using="gist", ops={"cidr_or_ip":"inet_ops"})` (**no** partial predicate); `UNIQUE(service_id, priority)` on `allow_rule`; `UNIQUE(tenant_id, lower(name))` on service; CHECK `committed_clean_gbps >= 0 AND committed_clean_gbps <= ceiling_clean_gbps`; CHECK port ranges (`0..65535`, `lo<=hi`); CHECK blacklist `scope↔service_id` XOR; partial `UNIQUE(service_id, source_cidr) WHERE scope='service'` + `UNIQUE(source_cidr) WHERE scope='global'`; `UNIQUE(service_id, source_cidr)` on whitelist. FKs: `protected_service.tenant_id → tenant.id ON DELETE RESTRICT`; children `service_id → protected_service.id ON DELETE CASCADE`; `created_by/allocated_by → user.id ON DELETE SET NULL`. Hand-edited Alembic revision (`down_revision` = tenant-cidr head) — autogenerate won't emit the GiST opclass or partial uniques.
**Where**: `control-plane/app/db/models.py` (modify), `control-plane/migrations/versions/*_service_rule_list.py`, `control-plane/tests/integration/test_service_models.py`
**Depends on**: None (reuses `Base`/`Tenant`/`User`/`AllocatedCIDR`)
**Reuses**: `Base`, `Tenant`, `User`, `AllocatedCIDR`; verified `inet_ops` idiom (AD-010); design "Data Models" + "Two enforced invariants"
**Requirement**: SRL-03 (CHECK), SRL-04/40 (dest exclusion), SRL-13 (FK CASCADE), SRL-16/38 (unique priority), SRL-43 (committed≤ceiling incl. equality/0); schema for SRL-01/22/26/28
**Tools**: Bash, Write/Edit · Skill: `coding-guidelines`
**Done when**:
- [x] `alembic upgrade head` creates all 5 tables with a constraint literally named `protected_service_dest_no_overlap` (verify via `pg_constraint`); **no `btree_gist`** required
- [x] Integration: two overlapping `cidr_or_ip` inserts → second raises `ExclusionViolation` (SRL-04); an overlapping insert while the first service is `enabled=false` still violates (services reserve space regardless of enabled); deleting the first frees the space (SRL-40)
- [x] `committed > ceiling` → CHECK violation; `committed == ceiling` and `committed == 0` accepted (SRL-03/43); duplicate `(service_id, priority)` → violation (SRL-16/38); out-of-range port CHECK fires (SRL-19 DB backstop)
- [x] Deleting a `protected_service` cascades its `allow_rule`/`whitelist_entry`/`blacklist_entry` rows (SRL-13); global blacklist row allows `service_id IS NULL` while service scope forbids it (scope CHECK)
- [x] Gate check passes: `ruff check . && ruff format --check . && mypy app/ && pytest -q` (full)
- [x] Test count: ≥8 tests pass (no silent deletions)
**Tests**: integration
**Gate**: full
**Commit**: `feat(service): service/plan/rule/list models + dest no-overlap constraint & migration`

---

### T3: Service service (ProtectedService + ServicePlan, version, dependency query)
**What**: `app/services/services.py` — `create_service` (assert dest ⊆ allocation via `cidr_in_tenant_allocation`; force default plan + 403 if `tenant_user` sizes committed/ceiling; INSERT `enabled=false`, `apply_status='pending'`, `version=1` + 1:1 plan; map `ExclusionViolation → OverlapError` 409, plan CHECK → `PlanInvariantError` 422; audit), `list_services`/`get_service` (admin any + owner annotation, tenant_user scoped), `update_service` (re-check dest ⊆ allocation + non-overlap; VIP ceiling; `bump_version`; audit), `set_enabled` (idempotent no-op; disable = dangerous audit; `bump_version`+`pending` on change), `size_plan` (admin-only; `committed≤ceiling`; oversubscription warning vs `node_clean_capacity`; audit), `delete_service` (409 if `enabled`; else hard-delete children cascade + dangerous audit), `bump_version` (`SELECT…FOR UPDATE`, `version+=1`, `apply_status='pending'`), `services_in_cidr(db, cidr)` (`WHERE cidr_or_ip <<= :cidr`). Domain exceptions in `app/services/errors.py` (or reuse existing).
**Where**: `control-plane/app/services/services.py`, `control-plane/tests/integration/test_services_service.py`
**Depends on**: T2
**Reuses**: `cidr_in_tenant_allocation` + `core/cidr` (tenant-cidr), `audit.record_event` (auth-rbac), models (T2); design "Service service"
**Requirement**: SRL-01, SRL-02, SRL-03, SRL-04, SRL-05, SRL-06, SRL-07, SRL-09, SRL-10, SRL-11, SRL-12, SRL-13, SRL-14, SRL-24, SRL-32 (`services_in_cidr`), SRL-33, SRL-34, SRL-35, SRL-36, SRL-42, SRL-43, SRL-44
**Tools**: Bash, Write/Edit · Skill: `coding-guidelines`
**Done when**:
- [x] `create_service` inside allocation persists (`enabled=false`, `pending`, `version=1`) + 1:1 plan + audit (SRL-01/34); dest outside allocation → `NotWithinAllocationError` (SRL-02/33); overlap → 409 (SRL-04); `committed>ceiling` → 422 (SRL-03); `tenant_user` sizing plan → 403, tenant create gets default plan (SRL-07)
- [x] `update_service` bumps `version`, resets `pending`, audits; editing `cidr_or_ip` to overlap → 409 (SRL-06/44); VIP ceiling persists (SRL-24)
- [x] `set_enabled(false)` = dangerous audit + `pending`; repeat = no-op, no duplicate audit (SRL-09/11); re-enable audited (SRL-10); disable retains whitelist/blacklist children (SRL-42)
- [x] `size_plan` admin-only; `Σ committed > node_clean_capacity` returns a non-blocking warning (SRL-36); `committed==ceiling`/`0` accepted (SRL-43)
- [x] `delete_service` on `enabled` → 409 (SRL-12); on `disabled` → hard-delete + children cascade + dangerous audit; both outcomes audited (SRL-13/14/35)
- [x] `services_in_cidr` returns services contained in a CIDR; empty otherwise (SRL-32 source); `list/get` scope admin vs tenant_user (SRL-05)
- [x] Gate check passes: `ruff check . && ruff format --check . && mypy app/ && pytest -q` (full)
- [x] Test count: ≥14 tests pass (no silent deletions)
**Tests**: integration
**Gate**: full
**Commit**: `feat(service): ProtectedService+ServicePlan lifecycle, version bump & CIDR dependency query`

---

### T4: Allow-rule service (≤16 row-lock, unique priority, overlap warning)
**What**: `app/services/rules.py` — `create_rule` (`SELECT…FOR UPDATE` parent service → reject `RuleLimitError` 409 if count≥16; `validate_port_range`; INSERT mapping unique violation → `DuplicatePriorityError` 409; compute `find_overlaps` warning; `bump_version`; audit), `update_rule`/`delete_rule`/`get_rule`/`list_rules` (scoped to owning service; `bump_version`+audit on write), `overlap_dry_run(db, service_id, candidate)` (read-only).
**Where**: `control-plane/app/services/rules.py`, `control-plane/tests/integration/test_rules_service.py`
**Depends on**: T1, T3
**Reuses**: `core/rulematch` (T1), `bump_version` (T3), models (T2), audit; design "Rule service"
**Requirement**: SRL-15, SRL-16, SRL-17, SRL-18, SRL-19, SRL-20, SRL-21, SRL-37, SRL-38, SRL-34
**Tools**: Bash, Write/Edit · Skill: `coding-guidelines`
**Done when**:
- [ ] `create_rule` persists + `bump_version` + audit (SRL-15/21/34); duplicate priority → 409 (SRL-16); 17th rule → `RuleLimitError` 409 (SRL-17); invalid port range → 422 (SRL-19)
- [ ] Overlapping rule → success + `warnings[]` naming the overlapped rule(s) (SRL-18); `overlap_dry_run` reports overlaps without writing (SRL-37)
- [ ] Concurrent inserts at the same priority → exactly one succeeds (SRL-38); concurrent 16→17 under the row-lock → exactly one 409 (SRL-17)
- [ ] `list/get/update/delete` scoped to the owning service (SRL-20)
- [ ] Gate check passes: `ruff check . && ruff format --check . && mypy app/ && pytest -q` (full)
- [ ] Test count: ≥9 tests pass (no silent deletions)
**Tests**: integration
**Gate**: full
**Commit**: `feat(rule): allow-rule service with ≤16 cap, unique priority & overlap warning`

---

### T5: List service (whitelist + service/global blacklist)
**What**: `app/services/lists.py` — `add_whitelist`/`remove_whitelist`/`list_whitelist` (source via `parse_ipv4_cidr` — arbitrary IPv4, reject IPv6/host-bits; keyed `(service_id, source_cidr)`; `bump_version`; audit), `add_blacklist`/`remove_blacklist`/`list_blacklist` (`scope='service'` on owned service → `bump_version`; `scope='global'` with `service_id=NULL`, `source='manual'` → no version bump; admin gate enforced at router; audit). Reject the containment check for sources (D-SRL-1 — sources are NOT scoped to allocation).
**Where**: `control-plane/app/services/lists.py`, `control-plane/tests/integration/test_lists_service.py`
**Depends on**: T3
**Reuses**: `core/cidr` (parse only, no containment), `bump_version` (T3), models (T2), audit; design "List service"
**Requirement**: SRL-22, SRL-23, SRL-25, SRL-26, SRL-27, SRL-28, SRL-30, SRL-34, SRL-41
**Tools**: Bash, Write/Edit · Skill: `coding-guidelines`
**Done when**:
- [ ] `add_whitelist` accepts arbitrary external IPv4 (`198.51.100.7/32`, `45.0.0.0/8`), rejects IPv6/host-bits → 422; source NOT required ⊆ allocation (SRL-22/23); `bump_version` + audit (SRL-34)
- [ ] `add_blacklist(scope='service')` on owned service persists + `bump_version` + audit (SRL-26); IPv6 → 422 (SRL-27); the same source may sit in both whitelist and blacklist with no conflict (SRL-41)
- [ ] `add_blacklist(scope='global')` persists `service_id=NULL`, `source='manual'`; list returns `source` discriminator (SRL-28/30)
- [ ] `list/remove` scoped (service entries → owning service; global → admin) (SRL-25)
- [ ] Gate check passes: `ruff check . && ruff format --check . && mypy app/ && pytest -q` (full)
- [ ] Test count: ≥9 tests pass (no silent deletions)
**Tests**: integration
**Gate**: full
**Commit**: `feat(list): whitelist + service/global blacklist service (arbitrary IPv4 sources)`

---

### T6: Wire TCA-16 dependency check into allocation revoke
**What**: Modify `app/services/allocations.py::revoke` — replace tenant-cidr's zero-stub dependency hook with a real check: lazy-import `services_in_cidr` (avoid module cycle) and refuse revoke with 409 + blocker service names when any active service sits inside the allocation being revoked.
**Where**: `control-plane/app/services/allocations.py` (modify), `control-plane/tests/integration/test_revoke_dependency.py`
**Depends on**: T3
**Reuses**: `services_in_cidr` (T3); design "TCA-16 wiring"
**Requirement**: SRL-32 (closes tenant-cidr TCA-16)
**Tools**: Bash, Write/Edit · Skill: `coding-guidelines`
**Done when**:
- [ ] Revoking an `AllocatedCIDR` that contains an active `ProtectedService` → 409 naming the blocking service(s) (SRL-32)
- [ ] After the service is deleted (T3), revoking the same CIDR → succeeds (tenant-cidr TCA-15/17 still hold)
- [ ] No import cycle: `python -c "import app.main"` succeeds
- [ ] Gate check passes: `ruff check . && ruff format --check . && mypy app/ && pytest -q` (full)
- [ ] Test count: ≥3 tests pass (no silent deletions)
**Tests**: integration
**Gate**: full
**Commit**: `feat(cidr): block CIDR revoke while services occupy the range (closes TCA-16)`

---

### T7: Service-ownership loader (deps)
**What**: `app/core/deps.py` (modify) — add `load_service_for_principal(service_id, principal) -> ProtectedService`: loads the service, applies `authorize_tenant_resource`, returns 404 (anti-enumeration) on cross-tenant / unknown. Declarative FastAPI dependency reused by the service/rule/list routers.
**Where**: `control-plane/app/core/deps.py` (modify), `control-plane/tests/integration/test_deps_service_loader.py`
**Depends on**: T3
**Reuses**: service service (T3), `authorize_tenant_resource`/`scope_to_tenant` (auth-rbac); design "Deps addition"
**Requirement**: SRL-31 (loader form; cross-tenant zero-leak)
**Tools**: Bash, Write/Edit · Skill: `coding-guidelines`
**Done when**:
- [ ] Owner (or admin) loads their service; `tenant_user` requesting another tenant's service id → 404, zero leak (SRL-31)
- [ ] Unknown service id → 404
- [ ] Gate check passes: `ruff check . && ruff format --check . && mypy app/ && pytest -q` (full)
- [ ] Test count: ≥3 tests pass (no silent deletions)
**Tests**: integration
**Gate**: full
**Commit**: `feat(service): fail-closed service-ownership loader dependency`

---

### T8: Services router (+ plan/enable/disable, schemas, mount)
**What**: `app/api/routers/services.py` — `POST/GET/PATCH/DELETE /services`, `POST /services/{id}/enable`, `POST /services/{id}/disable` (confirm flag), `PATCH /services/{id}/plan` (**require_admin**). CIDR field validated via `core/cidr` → 422; scope via `require_within_allocation`. Schemas in `app/api/schemas/services.py`; mount in `app/main.py`.
**Where**: `control-plane/app/api/routers/services.py`, `control-plane/app/api/schemas/services.py`, `control-plane/app/main.py` (modify), `control-plane/tests/integration/test_services_api.py`
**Depends on**: T3, T7
**Reuses**: service service (T3), `load_service_for_principal` (T7), `require_admin`/`require_within_allocation`/`get_current_user` (auth-rbac + tenant-cidr); design "API routers"
**Requirement**: SRL-01, SRL-03, SRL-04, SRL-05, SRL-06, SRL-07, SRL-08, SRL-09, SRL-10, SRL-11, SRL-12, SRL-13, SRL-14, SRL-24, SRL-31, SRL-35, SRL-36, SRL-39, SRL-40, SRL-44
**Tools**: Bash, Write/Edit · Skill: `coding-guidelines`
**Done when**:
- [ ] Create inside allocation → 201 (`pending`, `version=1`); outside → 403; overlap → 409; IPv6/host-bits `cidr_or_ip` → 422 (SRL-01/04/08/40); `committed>ceiling` → 422 (SRL-03)
- [ ] `tenant_user` `PATCH /plan` → 403; admin sizes plan, oversubscription → 200 + warning (SRL-07/36); list annotates owner for admin, scopes for tenant_user (SRL-05)
- [ ] `disable` (confirm) → dangerous audit, `pending`; idempotent repeat no-ops (SRL-09/11); enabling a **zero-rule** service → 200, no error (SRL-39)
- [ ] Delete enabled → 409; disable then delete → 204 + children cascade + audit (SRL-12/13/14); editing dest to overlap → 409 (SRL-44)
- [ ] Cross-tenant service access → 404 zero-leak (SRL-31)
- [ ] Gate check passes: `ruff check . && ruff format --check . && mypy app/ && pytest -q` (full)
- [ ] Test count: ≥12 tests pass (no silent deletions)
**Tests**: integration
**Gate**: full
**Commit**: `feat(service): services API with plan sizing, enable/disable & delete`

---

### T9: Rules router (+ overlap-check, schemas)
**What**: `app/api/routers/rules.py` — `POST/GET/PATCH/DELETE /services/{id}/rules`, `POST /services/{id}/rules/overlap-check`. Schemas in `app/api/schemas/rules.py`; mount in `app/main.py`.
**Where**: `control-plane/app/api/routers/rules.py`, `control-plane/app/api/schemas/rules.py`, `control-plane/app/main.py` (modify), `control-plane/tests/integration/test_rules_api.py`
**Depends on**: T4, T7
**Reuses**: rule service (T4), `load_service_for_principal` (T7); design "API routers"
**Requirement**: SRL-15, SRL-16, SRL-17, SRL-18, SRL-19, SRL-20, SRL-21, SRL-31, SRL-37
**Tools**: Bash, Write/Edit · Skill: `coding-guidelines`
**Done when**:
- [ ] Create rule → 201 + version bump; duplicate priority → 409; 17th → 409; invalid ports → 422; overlap → 201 + `warnings[]` (SRL-15/16/17/18/19/21)
- [ ] `overlap-check` dry-run reports overlaps, writes nothing (SRL-37); list/edit/delete scoped; cross-tenant → 404 (SRL-20/31)
- [ ] Gate check passes: `ruff check . && ruff format --check . && mypy app/ && pytest -q` (full)
- [ ] Test count: ≥8 tests pass (no silent deletions)
**Tests**: integration
**Gate**: full
**Commit**: `feat(rule): allow-rule API with overlap-check dry-run`

---

### T10: Lists router (whitelist + service blacklist, schemas)
**What**: `app/api/routers/lists.py` — `POST/GET/DELETE /services/{id}/whitelist`, `POST/GET/DELETE /services/{id}/blacklist` (scope=service). Schemas in `app/api/schemas/lists.py`; mount in `app/main.py`.
**Where**: `control-plane/app/api/routers/lists.py`, `control-plane/app/api/schemas/lists.py`, `control-plane/app/main.py` (modify), `control-plane/tests/integration/test_lists_api.py`
**Depends on**: T5, T7
**Reuses**: list service (T5), `load_service_for_principal` (T7); design "API routers"
**Requirement**: SRL-22, SRL-23, SRL-25, SRL-26, SRL-27, SRL-31, SRL-41
**Tools**: Bash, Write/Edit · Skill: `coding-guidelines`
**Done when**:
- [ ] Add whitelist arbitrary IPv4 → 201 + audit; IPv6 → 422 (SRL-22/23); add service blacklist → 201; IPv6 → 422 (SRL-26/27); same source in both lists coexists (SRL-41)
- [ ] list/delete scoped to owning service; another tenant's/service's list → 404 zero-leak (SRL-25/31)
- [ ] Gate check passes: `ruff check . && ruff format --check . && mypy app/ && pytest -q` (full)
- [ ] Test count: ≥7 tests pass (no silent deletions)
**Tests**: integration
**Gate**: full
**Commit**: `feat(list): service whitelist & blacklist API`

---

### T11: Global blacklist router (admin)
**What**: `app/api/routers/global_blacklist.py` — `POST/GET/DELETE /blacklist` (**require_admin**), arbitrary IPv4, `scope='global'`, `source='manual'`. Schemas reuse `app/api/schemas/lists.py`; mount in `app/main.py`.
**Where**: `control-plane/app/api/routers/global_blacklist.py`, `control-plane/app/main.py` (modify), `control-plane/tests/integration/test_global_blacklist_api.py`
**Depends on**: T5
**Reuses**: list service (T5), `require_admin` (auth-rbac); design "API routers"
**Requirement**: SRL-28, SRL-29, SRL-30
**Tools**: Bash, Write/Edit · Skill: `coding-guidelines`
**Done when**:
- [ ] Admin `POST /blacklist` → 201 (`source=manual`, `service_id=NULL`) + audit (SRL-28); list/delete work, `source` discriminator present (SRL-30)
- [ ] `tenant_user` on any `/blacklist` endpoint → 403, no side effect (SRL-29)
- [ ] Gate check passes: `ruff check . && ruff format --check . && mypy app/ && pytest -q` (full)
- [ ] Test count: ≥4 tests pass (no silent deletions)
**Tests**: integration
**Gate**: full
**Commit**: `feat(list): admin global blacklist API`

---

## Parallel Execution Map
```
Phase 1:  T1 [P]  ‖  T2                         (T1 unit/no-DB; T2 integration — no collision)
Phase 2:  T2 ──► T3
Phase 3:  T3 ──► {T4, T5, T6, T7}               (all integration → sequential; T4 also needs T1)
Phase 4:  {T3,T7}►T8 ; {T4,T7}►T9 ; {T5,T7}►T10 ; T5►T11   (integration → sequential)
```
Only **T1** is `[P]` — the sole unit-tested task (parallel-safe per TESTING.md). Every DB-touching task shares the single `compose.test.yml` Postgres and runs sequentially (AD-008).

---

## Validation — Check 1: Task Granularity

| Task | Scope | Status |
| --- | --- | --- |
| T1 | 1 module, 3 pure fns | ✅ Granular |
| T2 | 5 models + constraints + 1 migration (cohesive schema change) | ✅ Granular |
| T3 | 1 service (ProtectedService+ServicePlan are 1:1, one aggregate) | ✅ Granular |
| T4 | 1 service | ✅ Granular |
| T5 | 1 service | ✅ Granular |
| T6 | 1 function modification | ✅ Granular |
| T7 | 1 deps loader fn | ✅ Granular |
| T8 | 1 router (+schema/mount) | ✅ Granular |
| T9 | 1 router (+schema/mount) | ✅ Granular |
| T10 | 1 router (+schema/mount) | ✅ Granular |
| T11 | 1 router (+mount) | ✅ Granular |

## Validation — Check 2: Diagram ↔ Definition Cross-Check

| Task | Depends on (body) | Diagram arrows | Status |
| --- | --- | --- | --- |
| T1 | None | (root) | ✅ Match |
| T2 | None | (root) | ✅ Match |
| T3 | T2 | T2→T3 | ✅ Match |
| T4 | T1, T3 | T1→T4, T3→T4 | ✅ Match |
| T5 | T3 | T3→T5 | ✅ Match |
| T6 | T3 | T3→T6 | ✅ Match |
| T7 | T3 | T3→T7 | ✅ Match |
| T8 | T3, T7 | T3→T8, T7→T8 | ✅ Match |
| T9 | T4, T7 | T4→T9, T7→T9 | ✅ Match |
| T10 | T5, T7 | T5→T10, T7→T10 | ✅ Match |
| T11 | T5 | T5→T11 | ✅ Match |

Parallel check: `T1 [P]` shares no dependency with `T2` in Phase 1 ✅. No two tasks in any parallel set depend on each other.

## Validation — Check 3: Test Co-location

| Task | Code layer | Matrix requires | Task says | Status |
| --- | --- | --- | --- | --- |
| T1 | pure helpers (≈ security primitives) | unit | unit | ✅ OK |
| T2 | models + constraints | integration | integration | ✅ OK |
| T3 | service | integration | integration | ✅ OK |
| T4 | service | integration | integration | ✅ OK |
| T5 | service | integration | integration | ✅ OK |
| T6 | service (modify) | integration | integration | ✅ OK |
| T7 | deps/guards | integration | integration | ✅ OK |
| T8 | api router | integration | integration | ✅ OK |
| T9 | api router | integration | integration | ✅ OK |
| T10 | api router | integration | integration | ✅ OK |
| T11 | api router | integration | integration | ✅ OK |

All three checks pass — no restructuring required.

---

## Requirement Coverage

44 requirements (SRL-01..44) all map to tasks:

- SRL-01 (T3,T8) · 02 (T3,T8) · 03 (T2,T3,T8) · 04 (T2,T3,T8) · 05 (T3,T8) · 06 (T3,T8) · 07 (T3,T8) · 08 (T8) · 09 (T3,T8) · 10 (T3,T8) · 11 (T3,T8) · 12 (T3,T8) · 13 (T2,T3,T8) · 14 (T3,T8) · 15 (T4,T9) · 16 (T2,T4,T9) · 17 (T4,T9) · 18 (T1,T4,T9) · 19 (T1,T4,T9) · 20 (T4,T9) · 21 (T4,T9) · 22 (T5,T10) · 23 (T5,T10) · 24 (T3,T8) · 25 (T5,T10) · 26 (T5,T10) · 27 (T5,T10) · 28 (T5,T11) · 29 (T11) · 30 (T5,T11) · 31 (T7,T8,T9,T10) · 32 (T3,T6) · 33 (T3) · 34 (T3,T4,T5) · 35 (T3,T8) · 36 (T3,T8) · 37 (T1,T4,T9) · 38 (T2,T4) · 39 (T8) · 40 (T2,T3,T8) · 41 (T5,T10) · 42 (T3) · 43 (T2,T3) · 44 (T2,T3,T8).
- **Unmapped:** none. (All P1/P2 stories + edge cases covered.)

**Coverage:** 44 total, **44 mapped to tasks**, 0 unmapped ✅.

Cross-feature: SRL-32 closes tenant-cidr `TCA-16` (T6); SRL-02/33 wire auth-rbac `AUTH-14` (T3 via `cidr_in_tenant_allocation`).

---

## Tooling note (Execute phase)

Same uniform greenfield Python project as auth-rbac / tenant-cidr → built-in **Bash / Write / Edit** + `pytest`; apply the `coding-guidelines` skill during implementation. No external MCPs required. Diagrams via `mermaid-studio` (sources in `diagrams/`, already rendered). Execution delegates each task to a sub-agent (sequential; **T1** may run in parallel), per the skill's Sub-Agent Delegation model — **only when you approve and start Execute**. Because this feature reuses the auth-rbac + tenant-cidr runtime, **both must be executed (or at least their skeleton/migrations applied) before T2 onward** — T2's `down_revision` chains off tenant-cidr's head.
```
