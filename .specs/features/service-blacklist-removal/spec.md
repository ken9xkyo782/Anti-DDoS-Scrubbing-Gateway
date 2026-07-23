# Service-Blacklist Removal (B2 — Global Blacklist Only) Specification

> Source: [danh-gia-hieu-nang-data-plane.md §8.3 B2](../../../docs/danh-gia-hieu-nang-data-plane.md#83-nhóm-2--cắtthợp-nhất-tính-năng)
> — *"Bỏ blacklist theo-service nếu chỉ dùng global blacklist ⇒ deny-filter bớt một cặp bloom+LPM."*

> **⚠️ Amended by [design.md §0](design.md#0-finding-that-reframes-this-feature-️).** The design phase
> found that `bl_flags` is hardcoded to `0` in [applier.py:279](../../../control-plane/app/worker/applier.py#L279),
> so the service-blacklist branch has **never executed in production**. The affected passages below
> are marked *(amended)*. The removal decisions D1–D4 are unchanged; the perf justification is not.

## Problem Statement

`deny_filter_stage` ([blacklist.h:394](../../../data-plane/src/blacklist.h#L394)) evaluates **two**
independent blacklist scopes per whitelist-miss packet: global (bloom → LPM) and service-scoped
(bloom → LPM). The service branch costs an extra map-of-maps lookup plus a bloom peek plus an LPM
lookup on every packet whose service has `BL_F_ACTIVE` — on top of the ~15–20 map operations the
accept path already pays (§8.1). The service scope also carries a disproportionate amount of
cross-stack machinery for its value: two ARRAY_OF_MAPS pairs with four inner maps, a 12-byte LPM key
shape, a per-service flag byte in the service map value, a variable-length section in the apply wire
contract, a dedicated bloom-FP counter index, a DB scope enum with a check constraint and a
conditional unique index, three API routes, and a UI tab.

This feature removes the service scope end-to-end. Blocking becomes a single, global, admin-owned
control surface.

## Goals

- [ ] *(amended)* `deny_filter_stage` performs **zero** map lookups and holds **no code** for
      service-scoped blacklist. The removed branch was gated off in production, so the win is
      deleting an unenforced code path plus its per-apply cost (two fresh inner maps and their
      population, per service, on every config swap) — **not** a measurable ns/packet reduction.
- [ ] Exactly one blacklist scope exists in the system — global, admin-only — with no residual
      service-scoped data, routes, UI, or map storage.
- [ ] `make -C data-plane test` and the control-plane gate stay green, with the pre-existing reds
      recorded in memory unchanged (no new failures attributable to this work).
- [ ] The apply wire contract is versioned so that a mixed-version control plane / data plane
      rejects the snapshot outright rather than mis-parsing service records.

## Non-Goals / Out of Scope

| Item | Reason |
| --- | --- |
| Global blacklist behaviour, sizing, or the 1M-entry envelope | Unchanged; this feature only deletes the service scope. |
| Threat-feed sync pipeline | Feeds already write global-scope rows only ([feed_reconcile.py:306](../../../control-plane/app/services/feed_reconcile.py#L306)); no feed code path targets the service scope. |
| Whitelist / VIP scoping | Whitelist stays service-scoped (WLV-02 / AD-021). Only the blacklist scope collapses. |
| Bogon, hardcoded-amp, and blocked-port-bitmap filters | Same `deny_filter_stage` function, different requirement family (see [udp-amplification-config](../udp-amplification-config/spec.md)). |
| Other perf items A1–A4, B1, B3, B4, C1, C2, D1 | Separately specified; B2 must be measurable in isolation. |
| A replacement tenant-facing blocking capability | Explicit product decision (see GA-1): blocking becomes admin-only. |
| Restoring deleted service-scoped rows after upgrade | The migration is a one-way data deletion by decision (see GA-2). |

---

## Decisions Taken (pre-spec)

These were settled before writing this spec and are not open questions:

| # | Decision | Consequence |
| --- | --- | --- |
| D1 | **Full-stack removal**, not a runtime gate | Data plane, wire contract, DB schema, API, and UI all lose the service scope. Irreversible without a revert. |
| D2 | **Delete** existing service-scoped rows in the migration | *(amended)* Verdict-neutral: no source was ever blocked by a service-scoped entry, so deletion cannot change any packet's fate. |
| D3 | **No tenant-facing replacement** — blocking is admin-only by design | Tenants lose self-service blocking; they retain allow-rules, rate-limits, and whitelist/VIP. |
| D4 | **Remove the routes entirely** (404), no 410 deprecation window | `POST/GET/DELETE /services/{id}/blacklist` disappear from the router and from OpenAPI. |

---

## User Stories

### P1: Data plane drops the service blacklist branch ⭐ MVP

**User Story**: As a gateway operator, I want the deny filter to consult only the global blacklist,
so that every whitelist-miss packet stops paying for a second bloom+LPM pair.

**Why P1**: This is the entire point of B2. Without it the rest is cleanup with no benefit.

**Acceptance Criteria**:

1. WHEN a whitelist-miss packet reaches `deny_filter_stage` THEN the system SHALL evaluate the
   global blacklist only, and SHALL NOT perform any lookup against a service-scoped blacklist map.
2. WHEN the XDP object is built THEN the object SHALL contain no `service_blacklist_bloom*` and no
   `service_blacklist_lpm*` map ([blacklist.h:149-192](../../../data-plane/src/blacklist.h#L149)),
   verifiable via `bpftool prog show`/`bpftool map show` or the skeleton symbol set.
3. WHEN a source matches the global blacklist THEN the system SHALL drop it with reason
   `blacklist_drop` and `bl_state = BL_STATE_GLOBAL_HIT`, exactly as today.
4. WHEN a source matches nothing THEN the system SHALL set `BL_STATE_CLEAN` and continue to
   `allow_rule_stage`, exactly as today.
5. WHEN the code is compiled THEN `BL_STATE_SERVICE_HIT`, `BL_F_ACTIVE`, `BL_F_HAS_BROAD`,
   `struct sbl_lpm_key`, `struct sbl_bloom_key`, `SBL_*` constants, and the `sbl_bloom_key()` /
   `sbl_bloom_maybe()` / `sbl_lpm_hit()` helpers SHALL NOT be present in the tree.
6. WHEN the drop-reason ABI is inspected THEN the frozen indices (`bogon_drop` 4,
   `udp_amplification_drop` 7, `blacklist_drop` 8) SHALL be unchanged — `blacklist_drop` now covers
   global hits only.

**Independent Test**: Load the rebuilt object, seed a global blacklist entry, and confirm via
`smoke_apply.sh` that a matching source drops as `blacklist_drop` while a non-matching source passes;
confirm no `service_blacklist_*` pin appears under the pin dir after loader startup.

---

### P1: Apply wire contract v4 — service records lose `sbl` and `bl_flags` ⭐ MVP

**User Story**: As an operator running a rolling upgrade, I want a version-guarded snapshot format,
so that a control plane and a data plane on different versions fail loudly instead of writing
garbage into BPF maps.

**Why P1**: `sbl_count`/`sbl[]` is a variable-length trailing section of every service record
([apply_snapshot.h:62-63](../../../data-plane/src/apply_snapshot.h#L62)). Removing it while leaving
`APPLY_SNAPSHOT_SCHEMA_VERSION` at 3 would let an old writer's bytes be parsed as a new record and
silently corrupt the whole service table.

**Acceptance Criteria**:

1. WHEN the service record layout changes THEN `APPLY_SNAPSHOT_SCHEMA_VERSION` SHALL be raised from
   3 to 4 ([apply_snapshot.h:14](../../../data-plane/src/apply_snapshot.h#L14)).
2. WHEN a `SERVICE_FULL` snapshot is serialized THEN the per-service record SHALL omit the
   `sbl_count: le32` field and the `sbl[]` array entirely.
3. WHEN a `SERVICE_FULL` snapshot is serialized THEN the `bl_flags: u8` field SHALL be removed from
   the record, and `APPLY_SNAPSHOT_SERVICE_FIXED_SIZE` SHALL be adjusted accordingly (67 → 66 if
   `bl_flags` is dropped rather than reserved; the design phase settles reserved-vs-dropped for both
   the wire field and `struct service_val.bl_flags` at [service.h:17](../../../data-plane/src/service.h#L17)).
4. WHEN `xdpgw-apply` reads a snapshot whose `schema_version` is not 4 THEN it SHALL reject the
   snapshot before touching any data-plane map and SHALL exit non-zero with a version-mismatch
   message.
5. WHEN a `GLOBAL_DENY` snapshot is applied THEN its layout and behaviour SHALL be unchanged —
   only the `SERVICE_FULL` kind is affected.
6. WHEN the golden fixtures are regenerated
   ([gen_apply_snapshot_golden.py](../../../data-plane/tests/fixtures/gen_apply_snapshot_golden.py))
   THEN `test_snapshot.c` SHALL assert against v4 records with no `sbl_count` field.

**Independent Test**: Feed a v3 snapshot to the rebuilt `xdpgw-apply` and observe a non-zero exit
with maps untouched; feed a freshly generated v4 snapshot and observe a successful apply.

---

### P1: Schema migration deletes service-scoped rows ⭐ MVP

**User Story**: As an operator upgrading the control plane, I want the migration to remove
service-scoped blacklist data and its schema support, so that the only reachable blacklist scope is
global.

**Why P1**: Leaving rows behind with no reader would mean silently-inert blocking policy — the worst
failure mode of the three options considered.

**Acceptance Criteria**:

1. WHEN migration `0014` runs THEN it SHALL delete every `blacklist_entry` row with
   `scope = 'service'`, and SHALL report the deleted row count in the migration log.
2. WHEN migration `0014` runs THEN it SHALL drop the service-scope schema support:
   `BlacklistEntry.service_id`, the `ck_blacklist_scope_service_id` check constraint
   ([models.py:722](../../../control-plane/app/db/models.py#L722)), and the
   `uq_blacklist_service_source_cidr` unique index ([models.py:1041](../../../control-plane/app/db/models.py#L1041)).
3. WHEN migration `0014` runs THEN the `uq_blacklist_global_source_cidr` partial unique index and
   every global-scope row SHALL survive unchanged.
4. WHEN migration `0014` runs THEN `feed_blacklist_assertion` rows SHALL be unaffected — every
   assertion references a global-scope entry by construction.
5. WHEN the ORM is loaded after migration THEN `ProtectedService.blacklist_entries`
   ([models.py:539](../../../control-plane/app/db/models.py#L539)) SHALL no longer exist, and
   `BlacklistScope` SHALL admit `global` only (see GA-3 for the `scope` column's fate).
6. WHEN a protected service is deleted after migration THEN the delete SHALL succeed with no
   blacklist cascade involved.
7. WHEN migration `0014` is downgraded THEN it SHALL restore the schema objects and SHALL document
   that deleted rows are **not** recoverable.

**Independent Test**: Seed a DB with both global and service-scoped entries, run `alembic upgrade
head`, then assert: service rows gone, global rows intact, feed assertions intact, service delete
works.

---

### P1: Service blacklist API and UI removed ⭐ MVP

**User Story**: As an API client and as a tenant user, I want the removed capability to be absent
rather than present-but-broken, so that I discover the change immediately.

**Why P1**: An endpoint that accepts writes into a table nothing reads is a silent security
regression — the operator believes traffic is blocked when it is not.

**Acceptance Criteria**:

1. WHEN a client calls `POST`, `GET`, or `DELETE /services/{service_id}/blacklist`
   ([lists.py:79-140](../../../control-plane/app/api/routers/lists.py#L79)) THEN the API SHALL
   respond `404 Not Found` because the routes no longer exist.
2. WHEN the OpenAPI document is generated THEN it SHALL contain no service-blacklist path, and
   `BlacklistEntryResponse` SHALL no longer expose a `service_id`/scope discriminator that can be
   `service`.
3. WHEN `/blacklist` (global) is called by an admin THEN it SHALL behave exactly as today
   ([global_blacklist.py](../../../control-plane/app/api/routers/global_blacklist.py)), including
   `require_admin` enforcement.
4. WHEN a non-admin principal calls any blacklist endpoint THEN the system SHALL deny the request —
   there is no longer any blacklist mutation a tenant can perform.
5. WHEN a tenant opens the service detail page THEN the Blacklist tab SHALL be absent
   ([BlacklistTab.tsx](../../../control-plane/frontend/src/features/config/services/BlacklistTab.tsx),
   [ServiceDetailPage.tsx:181](../../../control-plane/frontend/src/features/config/services/ServiceDetailPage.tsx#L181)),
   and no build-time reference to the removed hook/route SHALL remain in
   [useLists.ts](../../../control-plane/frontend/src/hooks/resources/useLists.ts) or
   [types.ts](../../../control-plane/frontend/src/api/types.ts).
6. WHEN an admin opens the global blacklist page THEN it SHALL work unchanged
   ([GlobalBlacklistPage.tsx](../../../control-plane/frontend/src/features/config/global-blacklist/GlobalBlacklistPage.tsx)).
7. WHEN `list_service.add_blacklist` / `list_blacklist` / `remove_blacklist`
   ([lists.py:125,199,251,310](../../../control-plane/app/services/lists.py#L125)) are inspected
   THEN the `scope == BlacklistScope.service` branches SHALL be gone, and audit events SHALL still be
   recorded for global mutations.

**Independent Test**: `pytest` the lists/global-blacklist integration suites (service routes assert
404, global routes assert current behaviour), and run the frontend test suite with the
Blacklist-tab cases removed from `ServiceDetailPage.test.tsx`.

---

### P2: Bloom-FP telemetry surface collapses to two counters

**User Story**: As an operator reading `dpstat`, I want the bloom false-positive panel to show only
counters that can still be produced, so that a permanently-zero row does not read as "healthy".

**Why P2**: Cosmetic/observability rather than functional, but a dead `BLOOM_STAT_MAX` slot is
compiled into `dpstat`, the telemetry snapshot JSON, and the UI panel — leaving it is confusing.

**Acceptance Criteria**:

1. WHEN the data plane is built THEN `BLOOM_FP_SERVICE` SHALL be removed and `BLOOM_STAT_MAX` SHALL
   be 2 ([blacklist.h:42-43](../../../data-plane/src/blacklist.h#L42)).
2. WHEN `dpstat` prints or emits JSON THEN it SHALL report `whitelist` and `global_blacklist` only
   ([dpstat.c:186,650](../../../data-plane/tools/dpstat.c#L186)).
3. WHEN the telemetry reader ingests a node snapshot THEN it SHALL accept a two-key `bloom_stats`
   object without error ([telemetry_reader.py:118](../../../control-plane/app/worker/telemetry_reader.py#L118)).
4. WHEN the UI renders `BloomFpPanel` against a **historical** `node_health.bloom_stats` row that
   still contains a `service_blacklist` key THEN it SHALL render without crashing (unknown keys are
   tolerated or ignored, not assumed present)
   ([BloomFpPanel.tsx:11](../../../control-plane/frontend/src/components/BloomFpPanel.tsx#L11)).
5. WHEN `telemetry_snapshot_golden.json` is regenerated THEN it SHALL contain the two-key shape and
   the golden test SHALL pass.
6. WHEN the `bloom_hit_lpm_miss` alert source runs
   ([alert_sources.py:184](../../../control-plane/app/worker/alert_sources.py#L184)) THEN its
   behaviour SHALL be unchanged.

**Independent Test**: Run `dpstat --json` against a loaded object and assert the key set is exactly
`{whitelist, global_blacklist}`; render `BloomFpPanel` in a unit test with a legacy three-key object
and assert no throw.

---

### P2: Loader seed path drops `XDPGW_SEED_SBL_CIDR`

**User Story**: As a developer running smoke tests, I want the seed-from-env path to stop advertising
a knob that no longer does anything.

**Why P2**: Test-harness ergonomics; nothing in production depends on it.

**Acceptance Criteria**:

1. WHEN the loader starts THEN it SHALL NOT read `XDPGW_SEED_SBL_CIDR`, SHALL NOT pin
   `service_blacklist_bloom` / `service_blacklist_lpm`, and SHALL NOT print them in usage
   ([loader.c:38,134,196,255,308,540-569,843-884](../../../data-plane/loader/loader.c#L38)).
2. WHEN `XDPGW_SEED_GBL_CIDR` is set THEN global seeding SHALL work exactly as today.
3. WHEN the smoke scripts run THEN any `XDPGW_SEED_SBL_CIDR` usage SHALL have been removed from
   `data-plane/tests/*.sh` and `apply_smoke.py`, with the affected assertions retargeted at the
   global scope.
4. WHEN `bulk_blacklist.c` runs under the gated 1M test THEN it SHALL still exercise the global
   bloom+LPM path unchanged ([bulk_blacklist.c:227](../../../data-plane/tests/bulk_blacklist.c#L227)).

**Independent Test**: `make -C data-plane test` plus a root run of `smoke_apply.sh` and
`smoke_global_apply.sh`.

---

### P2: Documentation reflects a single blacklist scope

**User Story**: As anyone reading the PRD or the specs, I want the documented product to match the
shipped one.

**Why P2**: Doc drift here is high-consequence — PRD §6.6 is the contract that says tenants can
blacklist.

**Acceptance Criteria**:

1. WHEN [PRD.md §6.6](../../../PRD.md#66-blacklist) is read THEN it SHALL describe one global,
   admin-owned scope, and §4/§5 tenant capability lines (PRD.md:72, 117, 123, 136, 149) SHALL no
   longer promise tenant-scoped blacklist.
2. WHEN [PRD.md §6.5](../../../PRD.md#L198) whitelist-bypass text is read THEN the "service
   blacklist" bypass clause SHALL be removed while the global-blacklist bypass clause is kept.
3. WHEN [danh-gia-hieu-nang-data-plane.md §8.3](../../../docs/danh-gia-hieu-nang-data-plane.md#L157)
   is read THEN B2 SHALL be marked done with the measured before/after ns-per-packet delta.
4. WHEN [ROADMAP.md](../../project/ROADMAP.md) and the
   [blacklist-filters spec](../blacklist-filters/spec.md) are read THEN the superseded BLK
   requirements covering the service scope (notably BLK-03/BLK-04 and the BL-02 scoping posture)
   SHALL be annotated as superseded by this feature rather than silently contradicted.
5. WHEN [data-plane/README.md](../../../data-plane/README.md) is read THEN the map inventory and the
   seed-env table SHALL no longer list the service-blacklist maps or `XDPGW_SEED_SBL_CIDR`.

**Independent Test**: Grep the tree for `service blacklist`, `sbl`, `BlacklistScope.service`, and
`XDPGW_SEED_SBL_CIDR`; the only surviving hits should be in this spec and in historical
`.specs/features/*` documents marked as superseded.

---

### P3: Perf claim corrected and no-regression confirmed *(amended)*

**User Story**: As the person who proposed B2, I want the perf doc to state what this change actually
bought, so nobody plans future work against a number that was never real.

**Acceptance Criteria**:

1. WHEN `make -C data-plane bench` is run before and after THEN the result SHALL be **within ±1%**
   of the 2026-07-23 baseline on every path, and that no-regression result SHALL be recorded.
2. WHEN the benchmark is run THEN it SHALL use the same repeat/rounds settings as the baseline so
   the comparison is like-for-like.
3. WHEN `docs/danh-gia-hieu-nang-data-plane.md` §8.3/§8.6 is updated THEN it SHALL state explicitly
   that **B2's per-packet saving is zero as configured** — the branch was gated off by
   `bl_flags = 0` — and that the "~15–25%" row 5 estimate is carried by A4 and B1 alone.
4. WHEN the release note is written THEN it SHALL lead with "an unenforced API surface is removed",
   not "a feature is withdrawn".

---

## Edge Cases

- WHEN a control plane at schema v3 sends a snapshot to a data plane expecting v4 (or vice versa)
  THEN the reader SHALL reject it wholesale — no partial map writes, active slot unchanged.
- WHEN the upgrade is rolled out THEN the ordering SHALL be documented and enforced by the version
  check: applying either half alone leaves the gateway serving the previously-applied snapshot
  rather than a half-written one.
- WHEN the migration runs against a DB that has **zero** service-scoped rows THEN it SHALL succeed
  and log a deleted count of 0.
- WHEN the migration runs against a DB whose service-scoped rows duplicate an existing global CIDR
  THEN deletion SHALL still succeed — no promotion or dedupe logic is involved (D2).
- *(amended)* WHEN service-scoped rows are deleted THEN **no traffic verdict SHALL change**, because
  no such row was ever enforced. The release note SHALL say so explicitly, so that an operator who
  expected a traffic change does not go hunting for one.
- WHEN an audit-log entry references a deleted service-scoped blacklist mutation THEN the audit row
  SHALL be retained; audit history is not rewritten by this feature.
- WHEN a `node_health` row written before the upgrade carries a three-key `bloom_stats` object THEN
  the API and UI SHALL render it without error (SBR-14).
- WHEN a stored API client calls the removed routes THEN it SHALL receive `404`, which is
  indistinguishable from an unknown `service_id` — this is an accepted consequence of D4 and SHALL
  be called out in the release note.
- WHEN a service currently has `bl_flags = BL_F_ACTIVE` in the live service map THEN the first v4
  apply SHALL rebuild the service map value without that flag; no in-place migration of live map
  values is required because apply rebuilds the inactive slot wholesale.

---

## Gray Areas for Design Phase

### GA-1: Tenant blocking capability — closed

Decided (D3): no replacement. Tenants keep allow-rules, service rate-limits, and whitelist/VIP. This
is a **product capability reduction** and must appear in the release note, not only in the PRD diff.

### GA-2: Data deletion — closed

Decided (D2): delete. Design must still decide whether the migration snapshots the deleted rows into
the migration log / an archive table for forensics, or deletes silently with only a count.
**Recommendation:** log the deleted `(service_id, source_cidr)` pairs at INFO so an operator can
manually re-add any of them to the global list.

### GA-3: Does the `scope` column survive?

Open. Two options:

- **(a) Keep `scope`, narrowed to a single `global` value.** Minimal churn: `feed_reconcile.py`
  filters on `scope = 'global'` in ~10 raw-SQL sites (lines 249, 265, 316, 333, 355, 370, 393, 430)
  and `uq_blacklist_global_source_cidr` is a partial index with `index_where scope = 'global'`.
  Those all keep working untouched. Cost: a column that can only hold one value.
- **(b) Drop `scope` entirely.** Cleaner end state, but requires rewriting every feed-reconcile
  statement and redefining the unique index as unconditional — more surface, more risk, no runtime
  benefit.

**Recommendation: (a) for this feature**, with (b) noted in STATE.md as a deferred cleanup. Needs a
call in design.

### GA-4: `bl_flags` — dropped from the wire or kept as a reserved byte?

Open. Dropping it changes `APPLY_SNAPSHOT_SERVICE_FIXED_SIZE` (67 → 66) and
`struct service_val`'s layout; keeping it as `reserved0` preserves both sizes and leaves room for the
A4 "feature active" bitmask that the perf doc proposes next.
**Recommendation: keep the byte, rename it `reserved0`, and require it to be written as 0 and ignored
on read** — the schema version bump is happening regardless, and A4 will want a flag byte.

---

## Requirement Traceability

| Requirement ID | Story | Phase | Status |
| --- | --- | --- | --- |
| SBR-01 | P1: Data plane drops branch | Design | Pending |
| SBR-02 | P1: Data plane drops branch (maps removed) | Design | Pending |
| SBR-03 | P1: Data plane drops branch (global behaviour preserved) | Design | Pending |
| SBR-04 | P1: Data plane drops branch (symbols/constants purged) | Design | Pending |
| SBR-05 | P1: Data plane drops branch (frozen drop-reason ABI) | Design | Pending |
| SBR-06 | P1: Wire contract v4 (version bump) | Design | Pending |
| SBR-07 | P1: Wire contract v4 (`sbl` section removed) | Design | Pending |
| SBR-08 | P1: Wire contract v4 (`bl_flags` / fixed size) | Design | Pending |
| SBR-09 | P1: Wire contract v4 (version rejection, no partial apply) | Design | Pending |
| SBR-10 | P1: Wire contract v4 (golden fixtures) | Design | Pending |
| SBR-11 | P1: Migration (delete service rows + count) | Design | Pending |
| SBR-12 | P1: Migration (drop constraint/index/`service_id`) | Design | Pending |
| SBR-13 | P1: Migration (global rows + feed assertions preserved) | Design | Pending |
| SBR-14 | P1: Migration (ORM relationship + enum narrowing) | Design | Pending |
| SBR-15 | P1: API/UI (service routes 404) | Design | Pending |
| SBR-16 | P1: API/UI (OpenAPI + schema cleanup) | Design | Pending |
| SBR-17 | P1: API/UI (global endpoints unchanged, admin-only) | Design | Pending |
| SBR-18 | P1: API/UI (Blacklist tab + hooks removed) | Design | Pending |
| SBR-19 | P1: API/UI (service-layer scope branches removed, audit kept) | Design | Pending |
| SBR-20 | P2: Bloom-FP surface (`BLOOM_STAT_MAX` = 2) | - | Pending |
| SBR-21 | P2: Bloom-FP surface (dpstat + telemetry + legacy-key tolerance) | - | Pending |
| SBR-22 | P2: Loader seed path | - | Pending |
| SBR-23 | P2: Smoke scripts retargeted to global scope | - | Pending |
| SBR-24 | P2: Documentation (PRD, perf doc, roadmap, README) | - | Pending |
| SBR-25 | P3: Measured perf delta recorded | - | Pending |

**ID format:** `SBR-[NUMBER]`
**Status values:** Pending → In Design → In Tasks → Implementing → Verified
**Coverage:** 25 total, 0 mapped to tasks, 25 unmapped ⚠️ (tasks phase not yet run)

---

## Success Criteria

- [ ] `make -C data-plane test` passes with no new failures (baseline: 137 passed).
- [ ] Control-plane gate passes with only the 6 pre-existing reds recorded in memory
      (2 M6-Alerting in-progress + 4 user-delete ordering-pollution).
- [ ] Frontend test suite passes with the Blacklist-tab cases removed.
- [ ] *(amended)* `make -C data-plane bench` shows **no regression** (within ±1% noise) versus the
      2026-07-23 baseline; a speedup is not expected and its absence is not a failure.
- [ ] `grep -rn "sbl_\|service_blacklist\|BlacklistScope.service\|XDPGW_SEED_SBL_CIDR"` over
      `data-plane/`, `control-plane/app/`, and `control-plane/frontend/src/` returns zero hits.
- [ ] A v3 snapshot is rejected by the v4 `xdpgw-apply` with maps provably untouched.
- [ ] `alembic upgrade head` then `alembic downgrade -1` completes without error on a DB seeded with
      both scopes.

---

## Sizing

**Large** — multi-component (eBPF data plane, apply wire contract, C tooling/loader, DB migration,
Python API + service layer, React SPA, docs, three test suites) with a cross-component versioned
contract change. Design and Tasks phases are both required; do not skip to Execute.
