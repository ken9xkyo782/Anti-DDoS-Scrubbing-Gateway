# Service-Blacklist Removal (B2) Tasks

**Spec**: [spec.md](spec.md) · **Design**: [design.md](design.md)
**Status**: Draft
**Baseline**: `B` = the dp-unit pass count reported by `make -C data-plane test` **immediately before
T1**. Measure it first; do not assume a number (ROADMAP says 137, TESTING.md says 130 — the docs
disagree, and T11 fixes that). All DP counts below are expressed relative to `B`.

---

## Sequencing Constraints (why the order is what it is)

Three couplings dictate the shape of this plan:

1. **`apply_snapshot_golden.bin` binds C and Python.** `make apply` runs `test_snapshot` against it
   ([Makefile:31](../../../data-plane/Makefile#L31)) *and* the control-plane asserts
   `serialize_node_snapshot` is byte-identical to it
   ([test_snapshot_serialize.py:11](../../../control-plane/tests/unit/test_snapshot_serialize.py#L11)).
   The wire change therefore **cannot** be split across DP and CP tasks — T5 is atomic by necessity.
2. **Readers before writers before definitions.** The sbl maps can only be deleted (T3) once nothing
   reads them (T1) and nothing writes them (T2). Doing it in any other order leaves a red tree.
3. **The wire bump is deferred to T5 on purpose.** T2 stops *programming* the maps while still
   parsing and discarding `sbl[]` at v3. That keeps T1–T4 green without touching the contract, so
   the risky atomic change lands alone and bisects cleanly.

---

## Execution Plan

### Phase 1 — Data plane (sequential; all tasks edit shared `blacklist.h` / `test_parse.c`)

```
T1 → T2 → T3 → T4
```

### Phase 2 — Wire contract (sequential, atomic, spans DP + CP)

```
T4 → T5
```

### Phase 3 — Control plane (sequential; integration tests share compose.test.yml)

```
T5 → T6 → T7 → T8
```

### Phase 4 — Observability + SPA (parallel)

```
        ┌→ T9  [P]  (dp-unit + CP unit)
T8 ─────┤
        └→ T10 [P]  (fe-unit; independent gate)
```

### Phase 5 — Docs + verification (sequential)

```
T9, T10 → T11 → T12
```

---

## Task Breakdown

### T1: Delete the service-blacklist branch from the deny stage

**What**: Remove the `service_blacklist:` branch and its three lookup helpers from
`deny_filter_stage`, and drop the `bl_flags` parameter that only existed to gate it.
**Where**: `data-plane/src/blacklist.h`, `data-plane/src/whitelist.h`, `data-plane/tests/test_parse.c`
**Depends on**: None
**Reuses**: The surviving global-blacklist branch as the shape to preserve verbatim
**Requirement**: SBR-01, SBR-03, SBR-05

**Tools**: MCP: NONE · Skill: `coding-guidelines`

**Done when**:

- [x] Baseline `B` recorded from `make -C data-plane test` before any edit
- [x] `deny_filter_stage(ctx, meta, slot)` — `bl_flags` parameter gone; `goto service_blacklist`
      (line 442) becomes `goto clean`; the `BL_F_ACTIVE` block (457-481) deleted
- [x] `sbl_bloom_key()`, `sbl_bloom_maybe()`, `sbl_lpm_hit()`, `BL_STATE_SERVICE_HIT` deleted
- [x] `whitelist.h`: `whitelist_miss()` loses the parameter; the local at line 359 and all five
      call sites (347, 366, 373, 382, 389) updated
- [x] dp-unit: `test_blacklist_service_scoped_hit_does_not_cross_service`,
      `test_blacklist_service_bloom_false_positive_counts`,
      `test_blacklist_missing_service_lpm_inner_fails_closed` deleted and unregistered
- [x] dp-unit: `test_blacklist_global_precedes_service_attribution` **rewritten** (not deleted) as a
      global-attribution case, so global `blacklist_drop` attribution stays covered
- [x] Helpers `set_service_bl_flags()`, `seed_service_blacklist_bloom_key()` deleted
- [x] sbl maps, key structs, `SBL_*`, `BL_F_*`, `BLOOM_FP_SERVICE` **retained** (writers still exist)
- [x] Gate passes: `make -C data-plane test`
- [x] Test count: `B - 3` pass (3 deleted, 1 rewritten in place, 0 added)

**Verify**: `make -C data-plane test`; then `grep -c "sbl_bloom_maybe\|sbl_lpm_hit\|BL_STATE_SERVICE_HIT" data-plane/src/*.h` → `0`

**Tests**: dp-unit · **Gate**: quick
**Commit**: `refactor(dp): drop the service-blacklist branch from deny_filter_stage`

---

### T2: Stop programming and seeding the service-blacklist maps

**What**: Remove every writer of the sbl bloom/LPM maps — the apply-tool programming loop and the
loader's env-seed path — while still parsing and discarding `sbl[]` at wire v3.
**Where**: `data-plane/tools/xdpgw-apply.c`, `data-plane/loader/loader.c`,
`data-plane/tests/smoke_*.sh`, `data-plane/tests/apply_smoke.py`
**Depends on**: T1
**Reuses**: The whitelist bloom/LPM programming path as the shape that stays
**Requirement**: SBR-22, SBR-23

**Tools**: MCP: NONE · Skill: `coding-guidelines`

**Done when**:

- [x] `xdpgw-apply.c`: sbl programming loop (760-785) deleted; `apply_write_service` loses its
      `sbl_bloom_fd`/`sbl_lpm_fd` parameters; both `outers[APPLY_SERVICE_BLACKLIST_*]` assignments
      (834-835, 912-913) deleted; enum members and `APPLY_SERVICE_OUTER_COUNT` 8→6; two pin macros
      (69-70) and two `apply_fds` members (137-138) deleted
- [x] `parse_service` still reads `sbl_count`/`sbl[]` and discards them — **explicitly commented** as
      "consumed and dropped; removed in schema v4" so the intermediate state is not mistaken for a bug
- [x] `loader.c`: pin macros (38-39), `set_pin_path` (196-198), `unpin_map` (255-256), `pin_map`
      (308-312), usage fragment (134), env parse (540, 558-569), `seed_service_blacklist_from_env`
      (843-884) + its call (1046), the `sbl_enabled` disjunct (994), `val.bl_flags = sbl_flags`
      (1023), and the three seed-struct fields (70-76) all deleted
- [x] `XDPGW_SEED_GBL_CIDR` and `XDPGW_SEED_BLOCKED_PORT` still work
- [x] No `XDPGW_SEED_SBL_CIDR` remains in `tests/*.sh` or `apply_smoke.py`; affected assertions
      retargeted at the global scope
- [x] Gates pass: `make -C data-plane bpf skel loader apply dpstat` and `make -C data-plane test`
- [x] Slotted config-map set changed ⇒ `sudo make -C data-plane applybulk` passes (1000-service
      build/verify/flip < 5 s, one `active_config` flip, feed maps carried forward)
- [x] Test count: `B - 3` pass (unchanged from T1)

**Verify**: after `sudo ./build/xdp_gateway_loader`, `ls /sys/fs/bpf/xdp_gateway/` shows no
`service_blacklist_*` pin

**Tests**: dp-unit · **Gate**: build + quick (+ privileged `applybulk`)
**Commit**: `refactor(dp): stop programming and seeding service-blacklist maps`

---

### T3: Delete the service-blacklist map definitions and key structs

**What**: Remove the now-unreferenced sbl map definitions, key structs, and constants from the header.
**Where**: `data-plane/src/blacklist.h`
**Depends on**: T1 (no reader), T2 (no writer)
**Reuses**: The global map definitions as the surviving pattern
**Requirement**: SBR-02, SBR-04

**Tools**: MCP: NONE · Skill: `coding-guidelines`

**Done when**:

- [x] `service_blacklist_bloom{,_0,_1}` and `service_blacklist_lpm{,_0,_1}` definitions (149-192) deleted
- [x] `struct sbl_lpm_key`, `struct sbl_bloom_key` (51-59) and both `_Static_assert`s (69-72) deleted
- [x] `SBL_BLOOM_PREFIX`, `SBL_BLOOM_MAX_ENTRIES`, `SBL_BLOOM_HASHES`, `SBL_LPM_MAX_ENTRIES` (14-17)
      and `enum bl_service_flags` (34-37) deleted
- [x] The M4 build-contract comment (76-92) loses its service-scope bullet and the `BL_F_ACTIVE`
      half of the flags bullet
- [x] Object still loads: no `service_blacklist_*` map in the built skeleton
- [x] Gates pass: `make -C data-plane bpf skel loader apply dpstat` and `make -C data-plane test`
- [x] Test count: `B - 3` pass

**Verify**: `bpftool map show | grep -c service_blacklist` → `0` with the loader attached

**Tests**: dp-unit · **Gate**: build + quick
**Commit**: `refactor(dp): delete service-blacklist map definitions and key structs`

---

### T4: Rename `service_val.bl_flags` to `reserved0` and enforce zero

**What**: Turn the dead gate byte into an explicitly reserved, must-be-zero field (D-SBR-2).
**Where**: `data-plane/src/service.h`, `data-plane/tools/xdpgw-apply.c`,
`data-plane/loader/loader.c`, `data-plane/tests/test_snapshot.c`, `data-plane/tests/apply_smoke.py`
**Depends on**: T2
**Reuses**: The existing `_pad` / `gbl_meta._pad` reserved-byte convention
**Requirement**: SBR-08 (struct half)

**Tools**: MCP: NONE · Skill: `coding-guidelines`

**Done when**:

- [x] `struct service_val`: `__u8 bl_flags` → `__u8 reserved0`; the `sizeof == 8` static assert is
      **unchanged and still passes**
- [x] `cfg_service.bl_flags` → `reserved0`; `parse_service` rejects a record with `reserved0 != 0`
      (returns `-1` before any map write)
- [x] `apply_write_service` initializes `.reserved0 = 0`
- [x] Verify pass compares `value.reserved0 != 0` in place of the old `bl_flags` comparison (1127)
- [x] `loader.c` no longer assigns the field from seed state
- [x] `test_snapshot.c:50` asserts `reserved0 == 0`
- [x] Wire layout **byte-identical** to before this task — `APPLY_SNAPSHOT_SERVICE_FIXED_SIZE` still
      67, golden fixture **not** regenerated here
- [x] Gates pass: `make -C data-plane bpf skel loader apply dpstat` and `make -C data-plane test`
- [x] Test count: `B - 3` pass

**Verify**: `make -C data-plane apply` (runs `test_snapshot` against the **unmodified** golden blob)

**Tests**: dp-unit · **Gate**: build + quick
**Commit**: `refactor(dp): reserve the dead service-blacklist gate byte as reserved0`

---

### T5: Wire contract v4 — drop `sbl_count`/`sbl[]`, bump both sides, regenerate the golden ⚠️

**What**: The single atomic contract change: remove the trailing service-blacklist section from the
`SERVICE_FULL` record and move both the C reader and the Python writer to schema version 4.
**Where**: `data-plane/src/apply_snapshot.h`, `data-plane/tools/xdpgw-apply.c`,
`data-plane/tests/fixtures/gen_apply_snapshot_golden.py`,
`data-plane/tests/fixtures/apply_snapshot_golden.bin`, `data-plane/tests/test_snapshot.c`,
`data-plane/tests/test_parse.c`, `control-plane/app/worker/applier.py`,
`control-plane/tests/unit/test_snapshot_serialize.py`
**Depends on**: T4
**Reuses**: The existing version guard at `xdpgw-apply.c:745` — no new guard is written
**Requirement**: SBR-06, SBR-07, SBR-08, SBR-09, SBR-10

> **Highest-risk task in the plan.** It is atomic because the golden fixture binds the C parser and
> the Python serializer; splitting it reds one suite or the other. Land it alone.

**Tools**: MCP: NONE · Skill: `coding-guidelines`

**Done when**:

- [x] `APPLY_SNAPSHOT_SCHEMA_VERSION` 3 → 4 in `apply_snapshot.h` **and**
      `APPLY_SNAPSHOT_SCHEMA_VERSION = 4` in `applier.py:30` — both in this commit
- [x] Record doc updated: `bl_flags: u8` → `reserved0: u8 — must be written 0; readers reject
      non-zero`; the `sbl_count`/`sbl[]` lines (62-63) and
      `APPLY_SNAPSHOT_SERVICE_BLACKLIST_ENTRY_SIZE` (77) deleted;
      `APPLY_SNAPSHOT_SERVICE_FIXED_SIZE` still 67
- [x] `parse_service` drops the second `parse_source_list` call and the `sbl`/`sbl_count` fields;
      the per-service free path no longer frees `svc->sbl`
- [x] `applier.py`: the trailing `_append_source_list(payload, blacklist)` (315) and the now-unused
      `blacklist` local (275) removed; docstring says v4. `ServiceConfig.blacklist` **stays** for now
      (removed in T7) so this task touches only bytes
- [x] `gen_apply_snapshot_golden.py` updated (`bl_flags` kwarg → `reserved0`, trailing source list
      dropped from both fixtures) and `apply_snapshot_golden.bin` regenerated
- [x] `global_deny_snapshot_golden.bin` regenerated **only if** the shared header version made it
      stale; `GLOBAL_DENY` layout otherwise untouched
- [x] **New** dp-unit case: a v3 snapshot is rejected and `active_config` plus every config map is
      provably untouched
- [x] **New** dp-unit case: a record with `reserved0 != 0` is rejected before any map write
- [x] Gates pass: `make -C data-plane bpf skel loader apply dpstat`, `make -C data-plane test`,
      and CP `ruff check . && ruff format --check . && mypy app/ && pytest -q -m unit`
- [x] Test count: DP `B - 1` pass (`B - 3` from T1, `+2` new); CP unit suite unchanged in count

**Verify**: `make -C data-plane apply` (C parser vs new golden) **and**
`pytest -q -m unit -k snapshot_serialize` (Python serializer vs the same golden) both green

**Tests**: dp-unit + unit · **Gate**: build + quick (DP) + quick (CP)
**Commit**: `feat(apply)!: schema v4 — remove the service-blacklist section from service records`

---

### T6: Remove the service-blacklist API routes and service-layer scope branches

**What**: Delete the three `/services/{id}/blacklist` routes and collapse the list service to
global-only, so the removed capability returns 404 instead of silently storing unenforced rows.
**Where**: `control-plane/app/api/routers/lists.py`, `control-plane/app/services/lists.py`,
`control-plane/app/api/schemas/lists.py`, `control-plane/app/api/routers/global_blacklist.py`,
`control-plane/tests/integration/test_lists_api.py`, `test_lists_service.py`,
`test_global_blacklist_api.py`, `test_services_service.py`
**Depends on**: T5
**Reuses**: `get_admin_principal` / `require_admin` as the only surviving auth path
**Requirement**: SBR-15, SBR-16, SBR-17, SBR-19

**Tools**: MCP: NONE · Skill: `coding-guidelines`

**Done when**:

- [x] `routers/lists.py`: `add_service_blacklist`, `list_service_blacklist`,
      `remove_service_blacklist` (79-140), the now-unused `_blacklist_response` (160), and the
      `BlacklistEntry`/`BlacklistScope` imports deleted; whitelist routes untouched
- [x] `services/lists.py`: `add_blacklist`, `list_blacklist`, `remove_blacklist`,
      `_require_blacklist_entry` lose their `scope`/`service_id` parameters; each
      `if scope == BlacklistScope.service:` branch (125, 199, 251, 310) deleted;
      `_require_admin_actor` called unconditionally
- [x] Feed protections preserved: deleting a `source=feed` entry still 409s; assertion re-marking
      still runs; `record_event` audit still fires for global mutations
- [x] `BlacklistEntryResponse` drops `service_id`; `scope` is the literal `'global'`
- [x] Integration tests assert `404` on all three removed paths and unchanged behaviour on `/blacklist`
- [x] OpenAPI contains no service-blacklist path
- [x] Gate passes: `ruff check . && ruff format --check . && mypy app/ && pytest -q`
      (`compose.test.yml` up)
- [x] Test count: recorded; only the intentionally removed service-blacklist cases disappear, and
      the 6 pre-existing reds are unchanged

**Verify**: `curl -X POST .../services/<id>/blacklist` → `404`; `GET /blacklist` as admin → unchanged

**Tests**: integration · **Gate**: full
**Commit**: `feat(api)!: remove service-scoped blacklist endpoints`

---

### T7: Stop carrying service-blacklist rows through the applier

**What**: Remove the blacklist field, its two eager loads, and the build-log counter from the
snapshot builder.
**Where**: `control-plane/app/worker/applier.py`,
`control-plane/tests/integration/test_worker_applier.py`, `test_double_buffer_applier.py`
**Depends on**: T6
**Reuses**: The whitelist load/serialize path as the shape that stays
**Requirement**: SBR-07 (producer side)

**Tools**: MCP: NONE · Skill: `coding-guidelines`

**Done when**:

- [x] `ServiceConfig.blacklist` (68), both `selectinload(ProtectedService.blacklist_entries)`
      (224, 248), the `blacklist=` line in `_service_config` (356), and the `"blacklist_count"`
      build-log field (85) removed
- [x] `BlacklistEntry` import dropped if unused
- [x] Serialized bytes **identical** to T5's output (this task changes no wire byte) — the golden
      fixture is not regenerated
- [x] Gate passes: `ruff check . && ruff format --check . && mypy app/ && pytest -q`
- [x] Test count: recorded; applier suites green

**Verify**: `pytest -q -m unit -k snapshot_serialize` still matches the T5 golden byte-for-byte

**Tests**: integration · **Gate**: full
**Commit**: `refactor(worker): stop loading service-blacklist rows into apply snapshots`

---

### T8: Migration 0014 — delete service-scoped rows and drop their schema support

**What**: The one destructive step: delete `scope='service'` rows, drop the column, constraint, and
partial index that supported them, and pin the scope to global.
**Where**: `control-plane/migrations/versions/20260723_0014_drop_service_blacklist.py` (new),
`control-plane/app/db/models.py`, `control-plane/tests/integration/test_service_models.py`,
`test_feed_models.py`
**Depends on**: T6, T7 (nothing references the relationship or the service scope any more)
**Reuses**: [20260722_0013_blocked_udp_port.py](../../../control-plane/migrations/versions/20260722_0013_blocked_udp_port.py)
file/revision-chain shape
**Requirement**: SBR-11, SBR-12, SBR-13, SBR-14

**Tools**: MCP: NONE · Skill: `coding-guidelines`

**Done when**:

- [x] `down_revision = "20260722_0013"`
- [x] `upgrade()` in this exact order: log the `(service_id, source_cidr)` pairs at INFO with a count
      → `DELETE FROM blacklist_entry WHERE scope='service'` → drop
      `uq_blacklist_service_source_cidr` → drop `ck_blacklist_scope_service_id` → drop `service_id`
      → add `ck_blacklist_scope_global_only CHECK (scope = 'global')`
      *(delete must precede the CHECK or it fails on existing data)*
- [x] `downgrade()` reverses 6→3 and re-adds `service_id` nullable with `ondelete=CASCADE`; docstring
      states plainly that deleted rows are **not** restored
- [x] PG enum type `blacklist_scope` keeps both labels (D-SBR-1); the Python `BlacklistScope` keeps
      only `global_`
- [x] `models.py`: `service_id`, `ck_blacklist_scope_service_id`, `uq_blacklist_service_source_cidr`,
      and `ProtectedService.blacklist_entries` (539) removed; `ck_blacklist_scope_global_only` added
- [x] Integration test seeds **both** scopes, upgrades, then asserts: service rows gone, global rows
      intact, `feed_blacklist_assertion` rows intact, service delete succeeds with no cascade
- [x] Idempotency: migration on a zero-service-row DB succeeds and logs a count of 0
- [x] `alembic upgrade head` then `alembic downgrade -1` both clean
- [x] Gate passes: `ruff check . && ruff format --check . && mypy app/ && pytest -q`
- [x] Test count: recorded; feed suites unchanged (they never touch the service scope)

**Verify**: `alembic upgrade head && alembic downgrade -1 && alembic upgrade head` on the test DB

**Tests**: integration · **Gate**: full
**Commit**: `feat(db)!: drop service-scoped blacklist rows and schema support`

---

### T9: Collapse the bloom-FP surface to two counters [P]

**What**: Remove the third `bloom_stats` slot and its name-table entries, and regenerate the
telemetry golden fixture.
**Where**: `data-plane/src/blacklist.h`, `data-plane/tools/dpstat.c`,
`data-plane/tests/fixtures/telemetry_snapshot_golden.json`,
`control-plane/tests/unit/test_telemetry_reader.py`
**Depends on**: T8
**Reuses**: The surviving `whitelist` / `global_blacklist` entries as the pattern
**Requirement**: SBR-20, SBR-21

**Tools**: MCP: NONE · Skill: `coding-guidelines`

**Done when**:

- [ ] `BLOOM_FP_SERVICE` removed; `BLOOM_STAT_MAX` 3 → 2; `BLOOM_FP_WHITELIST=0` and
      `BLOOM_FP_GLOBAL=1` **keep their values** (D-SBR-6 — no remap anywhere)
- [ ] Both `dpstat.c` name tables (186-189, 650-654) drop the `service_blacklist` entry
- [ ] `dpstat snapshot --json` emits exactly `{whitelist, global_blacklist}` under `node.bloom_stats`
- [ ] `telemetry_snapshot_golden.json` regenerated with the two-key shape; the CP reader unit test
      passes against it
- [ ] `alert_sources.py` `bloom_hit_lpm_miss` behaviour unchanged
- [ ] Gates pass: `make -C data-plane bpf skel loader apply dpstat`, `make -C data-plane test`, and
      CP `ruff check . && ruff format --check . && mypy app/ && pytest -q -m unit`
- [ ] Test count: DP `B - 1` pass; CP unit count unchanged

**Verify**: `sudo ./build/dpstat snapshot --json | jq '.node.bloom_stats | keys'` →
`["global_blacklist","whitelist"]`

**Tests**: dp-unit + unit · **Gate**: build + quick (DP) + quick (CP)
**Commit**: `refactor(dp): collapse bloom false-positive stats to two counters`

---

### T10: Remove the Blacklist tab and its hooks from the SPA [P]

**What**: Delete the service-detail Blacklist tab, its three data hooks, and the scope type, keeping
the admin global-blacklist page and the legacy bloom label intact.
**Where**: `control-plane/frontend/src/features/config/services/BlacklistTab.tsx` (delete),
`ServiceDetailPage.tsx`, `ServiceDetailPage.test.tsx`, `hooks/resources/useLists.ts`,
`useLists.test.ts`, `api/types.ts`, `components/BloomFpPanel.tsx`
**Depends on**: T8
**Reuses**: The whitelist tab as the surviving sibling; `GlobalBlacklistPage` stays untouched
**Requirement**: SBR-18, SBR-21 (UI half)

**Tools**: MCP: NONE · Skill: `coding-guidelines`

**Done when**:

- [ ] `BlacklistTab.tsx` deleted
- [ ] `ServiceDetailPage.tsx`: import (16), `Tabs.Trigger` (111), `Tabs.Content` + child (180-181)
      removed, and the page description (71) no longer says "blacklists"
- [ ] `useLists.ts`: `useBlacklist`, `useAddBlacklist`, `useRemoveBlacklist` (56-101) removed
- [ ] `types.ts`: `BlacklistScope` narrowed to `'global'`; `BlacklistEntryResponse.service_id` removed
- [ ] `BloomFpPanel.tsx`: the `service_blacklist` label is **kept** with a comment marking it
      legacy-row-only (D-SBR-7); `BloomFpPanel.test.tsx`'s three-key case is **kept** and renamed to
      state it is a historical-row regression test
- [ ] `ServiceDetailPage.test.tsx` blacklist-tab cases (248, 427, 471) and the `useLists.test.ts`
      blacklist cases removed
- [ ] `GlobalBlacklistPage` and its hooks untouched and still green
- [ ] Gate passes: `cd control-plane/frontend && npm run lint && npm run typecheck && npm run test -- --run && npm run build`
- [ ] Test count: recorded (drops by exactly the removed cases; 225 was the last recorded total)

**Verify**: service detail page renders with Whitelist + Rules tabs and no Blacklist tab; admin
global blacklist page unchanged

**Tests**: fe-unit · **Gate**: fe
**Commit**: `feat(ui)!: remove the service blacklist tab`

---

### T11: Update documentation to a single blacklist scope

**What**: Bring PRD, perf doc, roadmap, data-plane README, TESTING.md, and the superseded
blacklist-filters requirements in line with the shipped system.
**Where**: `PRD.md`, `docs/danh-gia-hieu-nang-data-plane.md`, `data-plane/README.md`,
`.specs/codebase/TESTING.md`, `.specs/project/ROADMAP.md`, `.specs/features/blacklist-filters/spec.md`
**Depends on**: T9, T10
**Reuses**: AD-039 / AD-040 in STATE.md as the source of record
**Requirement**: SBR-24

**Tools**: MCP: NONE · Skill: `docs-writer`

**Done when**:

- [ ] PRD §6.6 describes one global admin-owned scope; §6.5 drops the "service blacklist" bypass
      clause but keeps the global one; tenant capability lines (72, 117, 123, 136, 149) no longer
      promise tenant-scoped blacklist
- [ ] `docs/danh-gia-hieu-nang-data-plane.md` §8.3 marks B2 done **and states that its per-packet
      saving is zero** because the branch was gated off by `bl_flags = 0`; §8.6 row 5 notes the
      "~15–25%" is carried by A4 and B1 alone
- [ ] `data-plane/README.md` map inventory and seed-env table drop the sbl maps and
      `XDPGW_SEED_SBL_CIDR`
- [ ] `TESTING.md` corrected: deny-stage conventions drop "service-scoped bloom/LPM maps"; the corpus
      line drops "global/service"; the stale "**v2** contract" reference becomes **v4**; the stale
      dp-unit count is reconciled with the measured one
- [ ] ROADMAP and blacklist-filters BLK-03/BLK-04 + the BL-02 posture annotated **superseded by
      this feature** rather than left contradicting it
- [ ] No doc still claims tenants can blacklist

**Verify**: `grep -rn "service blacklist\|service-scoped blacklist" PRD.md docs/ data-plane/README.md .specs/codebase/` returns only superseded-annotated hits

**Tests**: none (docs layer — matrix says none) · **Gate**: none
**Commit**: `docs: blacklist is global-only; record B2's true perf effect`

---

### T12: Full-stack verification and perf confirmation

**What**: Run every gate end to end, confirm no bench regression, and prove no residue remains.
**Where**: repo-wide (no source edits expected)
**Depends on**: T11
**Reuses**: The verification plan in design.md §8
**Requirement**: SBR-25 + all spec success criteria

**Tools**: MCP: NONE · Skill: NONE

**Done when**:

- [ ] `make -C data-plane test` → `B - 1` pass
- [ ] `sudo make -C data-plane smoke` green (redirect, fairness, apply, blocked-port, bypass)
- [ ] `sudo make -C data-plane applybulk` and `sudo make -C data-plane blbulk` green (map definitions
      changed ⇒ TESTING.md requires the blacklist scale gate)
- [ ] `make -C data-plane bench` **within ±1%** of the 2026-07-23 baseline, same repeat/rounds; a
      speedup is not expected and its absence is not a failure (spec P3, amended)
- [ ] CP full gate green except the 6 pre-existing reds on record (2 M6-Alerting + 4 user-delete
      ordering pollution) — each confirmed pre-existing, not new
- [ ] FE gate green
- [ ] `alembic upgrade head` → `downgrade -1` → `upgrade head` clean on a both-scopes seeded DB
- [ ] Residue grep over `data-plane/`, `control-plane/app/`, `control-plane/frontend/src/` for
      `sbl_|service_blacklist|BlacklistScope.service|XDPGW_SEED_SBL_CIDR` returns **only** the
      retained legacy UI label and its comment
- [ ] A v3 snapshot is rejected by the v4 apply tool with maps provably untouched
- [ ] STATE.md updated with measured counts and the bench result

**Verify**: all of the above, recorded in the execution summary

**Tests**: none (verification pass) · **Gate**: full
**Commit**: `chore: verify service-blacklist removal end to end`

---

## Check 1 — Task Granularity

| Task | Scope | Status |
| --- | --- | --- |
| T1 | 1 function's branch + its 2 call sites + its tests | ✅ Granular (cohesive: a callee signature change forces its callers) |
| T2 | 1 concept: remove all writers (2 files) | ✅ Granular |
| T3 | 1 file, definitions only | ✅ Granular |
| T4 | 1 field rename + its 4 references | ✅ Granular |
| T5 | 1 wire contract version | ⚠️ Largest task, **atomic by necessity** — the golden fixture binds C + Python (documented above) |
| T6 | 3 routes + their service-layer branches | ✅ Granular |
| T7 | 1 file, 5 line-level removals | ✅ Granular |
| T8 | 1 migration + its model mirror | ✅ Granular |
| T9 | 1 enum slot + its 2 name tables + 1 fixture | ✅ Granular |
| T10 | 1 component deleted + its 3 hooks | ✅ Granular |
| T11 | Docs only | ✅ Granular |
| T12 | Verification only, no edits | ✅ Granular |

---

## Check 2 — Diagram / Definition Cross-Check

| Task | `Depends on` (body) | Diagram shows | Status |
| --- | --- | --- | --- |
| T1 | None | phase 1 entry | ✅ Match |
| T2 | T1 | T1 → T2 | ✅ Match |
| T3 | T1, T2 | T2 → T3 (T1 transitive) | ✅ Match |
| T4 | T2 | T3 → T4 (T2 transitive via chain) | ✅ Match |
| T5 | T4 | T4 → T5 | ✅ Match |
| T6 | T5 | T5 → T6 | ✅ Match |
| T7 | T6 | T6 → T7 | ✅ Match |
| T8 | T6, T7 | T7 → T8 (T6 transitive) | ✅ Match |
| T9 | T8 | T8 → T9 `[P]` | ✅ Match |
| T10 | T8 | T8 → T10 `[P]` | ✅ Match |
| T11 | T9, T10 | T9, T10 → T11 | ✅ Match |
| T12 | T11 | T11 → T12 | ✅ Match |

T9 and T10 do not depend on each other and share no source file → `[P]` is valid.

---

## Check 3 — Test Co-location Validation

| Task | Code layer modified | Matrix requires | Task says | Status |
| --- | --- | --- | --- | --- |
| T1 | XDP verdict path | dp-unit | dp-unit | ✅ OK |
| T2 | Apply helper + loader | dp-unit (+ build) | dp-unit | ✅ OK |
| T3 | XDP map definitions | dp-unit (+ build) | dp-unit | ✅ OK |
| T4 | Map contract struct | dp-unit (+ build) | dp-unit | ✅ OK |
| T5 | Wire contract (C) + `app/worker/applier.py` serializer | dp-unit + unit | dp-unit + unit | ✅ OK |
| T6 | `app/api/routers/*` + `app/services/lists.py` | integration | integration | ✅ OK |
| T7 | `app/worker/applier.py` | integration | integration | ✅ OK |
| T8 | `app/db/models.py` + `migrations/` | integration | integration | ✅ OK |
| T9 | XDP + dpstat + CP telemetry reader | dp-unit + unit | dp-unit + unit | ✅ OK |
| T10 | `control-plane/frontend/src/` | fe-unit | fe-unit | ✅ OK |
| T11 | Documentation | none | none | ✅ OK |
| T12 | No code layer (verification) | none | none | ✅ OK |

No task defers its tests to a later task.

**Parallelism justification for `[P]` on T9/T10:** T9's CP half is `unit` (parallel-safe per the
Parallelism Assessment); T10's gate is `fe`, which TESTING.md states is independent of
`compose.test.yml` and may run alongside control-plane work when no shared source files are edited —
T9 and T10 share none. Every task with **integration** tests (T6, T7, T8) runs strictly sequentially.

---

## Requirement Coverage

| Req | Task | Req | Task |
| --- | --- | --- | --- |
| SBR-01 | T1 | SBR-14 | T8 |
| SBR-02 | T3 | SBR-15 | T6 |
| SBR-03 | T1 | SBR-16 | T6 |
| SBR-04 | T1, T3 | SBR-17 | T6 |
| SBR-05 | T1 | SBR-18 | T10 |
| SBR-06 | T5 | SBR-19 | T6 |
| SBR-07 | T5, T7 | SBR-20 | T9 |
| SBR-08 | T4, T5 | SBR-21 | T9, T10 |
| SBR-09 | T5 | SBR-22 | T2 |
| SBR-10 | T5 | SBR-23 | T2 |
| SBR-11 | T8 | SBR-24 | T11 |
| SBR-12 | T8 | SBR-25 | T12 |
| SBR-13 | T8 | | |

**Coverage: 25 of 25 mapped, 0 unmapped.**

---

## Rollout Note (carry into the release)

T5 through T8 are **not independently deployable**. The schema-v4 bump means a control plane and a
data plane from different sides of this change reject each other's snapshots (fail-closed: the
previously applied config keeps serving, apply jobs fail and retry). Ship the whole set together, and
tell operators that apply failures during the upgrade window are expected and self-healing.
