# Blacklist (Bloom + LPM) & Deny Filters — Tasks

**Design**: `.specs/features/blacklist-filters/design.md` (AD-023, APPROVED)
**Status**: Executing (2026-07-09) — T1, T2, T3, T4, T5, T6, T7 complete
**Baseline**: dp-unit suite **B = 68** (post-WLV, re-verified 2026-07-09: `make test` → 68 passed).
WLV is Executed/VERIFIED, so the A-BLK-5 execute gate is **satisfied**.
**Tools (per STATE Preferences)**: Skill `coding-guidelines` on all C/XDP code tasks (T1–T7); no
MCPs configured. T8 is docs-only.

---

## Execution Plan

### Phase 1: Contracts + behavior-neutral migration (T2 parallel-eligible)

```
T1 ──┐
     ├──→ T3
T2 [P]┘
```

T1 (contracts/maps, `src/*.h` only) and T2 (suite source migration, `tests/*` only) touch disjoint
files and neither changes behavior — T2 may run alongside T1.

### Phase 2: Stage behavior (Sequential — shared `src/blacklist.h` + `tests/test_parse.c`)

```
T3 → T4
```

### Phase 3: Loader, tools, scale gate, docs (Sequential — privileged gates not parallel-safe)

```
T4 → T5 → T6 → T7 → T8
```

T5 runs `sudo make smoke` and T7 runs the new privileged `make blbulk` — dp-integration is not
parallel-safe (shared veth/attach state). T8 needs T7's measured footprint number, so docs close
the chain.

---

## Task Breakdown

### T1: Contract header, maps & load de-risk

**What**: New `src/blacklist.h` with all contracts (`bl_lpm_key`, `sbl_lpm_key`, `sbl_bloom_key`,
`gbl_meta`, `gbl_flags`, `bl_service_flags`, `bl_state` values, all `GBL_*/SBL_*/BLOCKED_PORT_*`
constants per design Data Models, M4 build-contract comment block) and all map definitions
(`global_blacklist_bloom/lpm`, `service_blacklist_bloom/lpm`, `udp_blocked_port_bitmap` as
`ARRAY_OF_MAPS[2]` with BTF-static inners; `gbl_meta` slot-keyed `ARRAY[2]`; unslotted
`bloom_stats` `PERCPU_ARRAY[3]` + `bump_bloom_fp()`); the two pure-code checks
(`amp_port_hardcoded()` D-BLK-1 12-port switch, `bogon_src()` A-BLK-1 range compares) defined but
not yet called; `service_val` pad byte → `bl_flags`, `pkt_meta._pad` → `bl_state` (sizes 8/32
static-asserted, unchanged); header included from `xdp_gateway.bpf.c` (maps emitted, stage not
wired). **De-risk (fail-fast at this gate)**: the program loads with the `ARRAY`-inner bitmap
composition and the **1M-`max_entries`** LPM inners accepted by the kernel (the dp-unit runner
loads the full object, so `make test` is the load proof); fallback = slot-in-key single maps (same
external contract), rung recorded here + STATE if taken.
**Where**: `data-plane/src/blacklist.h` (new), `data-plane/src/service.h`,
`data-plane/src/pkt_meta.h`, `data-plane/src/xdp_gateway.bpf.c` (include only)
**Depends on**: None
**Reuses**: `whitelist.h` map-declaration shape (BTF-static bloom inners — composition proven by
WLV T1), `service_map` LPM-inner precedent, `sample.h` `sample_stats`/`bump_sample_stat` pattern,
`_Static_assert` size-pinning convention
**Requirement**: BLK-21; BLK-08 (map-definition half); BLK-09 (field); BLK-25 (load-de-risk half)

**Tools**: Skill `coding-guidelines`; MCP NONE

**Done when**:

- [x] `blacklist.h` matches design Data Models field-for-field, incl. the M4 contract comment
      (one-snapshot swap, bloom ⊇ LPM, 16..23 expansion band, </16 + over-fill ⇒ `GBL_F_HAS_BROAD`,
      replace-only blooms, omit disabled/expired rows)
- [x] `sizeof(service_val)==8`, `sizeof(pkt_meta)==32` still static-asserted; new key structs
      size-asserted (`sbl_bloom_key`==8)
- [x] Program **loads** with all six new maps present (1M LPM inners + ARRAY inners accepted, or
      fallback rung documented here + STATE)
- [x] Gate check passes: `make bpf skel loader dpstat && make test`
- [x] Test count: **68** pass (baseline unchanged — include-only)

**Completion (2026-07-09)**: Primary map-in-map design loaded successfully; generated skeletons
expose the new global/service blacklist bloom+LPM maps, bitmap maps, `gbl_meta`, and
`bloom_stats`. Gate `cd data-plane && make bpf skel loader dpstat && make test` → **68 passed**.
No fallback rung was needed.

**Tests**: dp-unit (load proof; no new cases)
**Gate**: build + quick

**Commit**: `feat(blacklist): add deny-filter map contracts and 1M-scale load de-risk`

---

### T2: Suite source migration off bogon space (behavior-neutral) [P]

**What**: `pkt_build.h` gains named non-bogon source constants (e.g. `TEST_SRC_PUB_A/B/C` from
`45.45.0.0/16` / `185.0.0.0/8` pools, matching the spec's examples); every existing dp-unit case
whose packet source (or whitelist-seed CIDR, moved together with its matching sources) falls inside
the A-BLK-1 bogon set migrates to a named constant in one mechanical sweep. Case *intent* is
untouched; `WL_TEST_BLOOM_*` probe constants stay (pre-parse hook path, never reaches the deny
stage). Because the deny stage is not yet wired, this task is **verdict-neutral by construction** —
the cleanest possible review: all 68 expectations identical before and after.
**Where**: `data-plane/tests/pkt_build.h`, `data-plane/tests/test_parse.c`
**Depends on**: None (disjoint files from T1; parallel-eligible)
**Reuses**: existing builders/case table in `test_parse.c`
**Requirement**: BLK-24 (migration half)

**Tools**: Skill `coding-guidelines`; MCP NONE

**Done when**:

- [x] Zero remaining bogon-range packet sources in the suite outside deliberately-bogon future
      cases (`grep` sweep for the A-BLK-1 ranges recorded in the commit message)
- [x] Whitelist-seeded cases moved as CIDR+source pairs (scoped matching intent intact)
- [x] Gate check passes: `make test`
- [x] Test count: **68** pass, expectations unmodified (verdict-neutral proof)

**Completion (2026-07-09)**: Default packet source and existing whitelist source/CIDR pairs moved
to named public-source constants. Sweep
`rg "0x0a000001|0xc6336407|0xc6336408|0xcb007109|0xc6336400|0xc6330000|192\\.0\\.2|198\\.51\\.100|203\\.0\\.113" data-plane/tests/test_parse.c data-plane/tests/pkt_build.h`
returned no matches. Gate `cd data-plane && make test` → **68 passed**.

**Tests**: dp-unit (migration of existing cases)
**Gate**: quick

**Commit**: `test(blacklist): migrate dp-unit sources off bogon space ahead of bogon check`

---

### T3: Deny stage — amp ports, bogon, bitmap + seam-B wire-in

**What**: `deny_filter_stage(ctx, meta, slot, bl_flags)` in `blacklist.h` implementing steps 1–3
(UDP `amp_port_hardcoded` → `bl_state=AMP_HARDCODED` + `record_drop(DR_UDP_AMPLIFICATION_DROP)`;
`bogon_src` → `BOGON` + `record_drop(DR_BOGON_DROP)`; UDP bitmap word/bit test on the pinned slot →
`AMP_BITMAP` + index 7; missing bitmap inner → `DR_MAP_ERROR`) then falling through to
`allow_rule_stage()` (global/service bands land in T4 behind their gates, which read as inactive
until then); seam-B rewire: `whitelist_miss()` calls the stage, `whitelist_stage()` +
`service_lookup_redirect()` re-signed to thread `const struct service_val *` / `bl_flags`; test
seed helper `seed_blocked_port(port)`. New dp-unit cases: hardcoded-port drop (representative
ports incl. 53), TCP src-53 passes the port filters, bogon drop per representative range (RFC 1918,
127/8, 224/4, TEST-NET), bitmap hit + adjacent-port pass + empty-bitmap pass, whitelisted source
sending from an amp port / bogon range redirects (VIP exception, BLK-15), non-UDP protocols
(ICMP/GRE) skip port checks, `bl_state` observability on each outcome.
**Where**: `data-plane/src/blacklist.h`, `data-plane/src/whitelist.h`,
`data-plane/src/xdp_gateway.bpf.c`, `data-plane/tests/test_parse.c`
**Depends on**: T1, T2
**Reuses**: `record_drop()`, `write_test_meta()`, pinned-slot discipline, `seed_*` helper patterns
**Requirement**: BLK-01 (position), BLK-10..16, BLK-22 (indices 4/7 half), BLK-09 (set-on-outcome),
BLK-07 (bitmap fail-closed half), BLK-24 (bogon-cases half)

**Tools**: Skill `coding-guidelines`; MCP NONE

**Done when**:

- [x] Baseline 68 + T2 migration pass **unmodified** (deny maps empty ⇒ verdicts identical;
      spot-check `bl_state==CLEAN` on one enabled-service rule-path case)
- [x] §8.2 in-band order proven: packet that is both amp-port and bogon drops index 7 (amp first);
      bogon + bitmap-port drops index 4 (bogon before bitmap)
- [x] Whitelist hit from amp port/bogon source redirects — the stage never runs on the hit path
- [x] Fail-closed: bitmap inner removed from outer → `map_error`
- [x] Program still loads native (branch-only checks pass the verifier)
- [x] Gate check passes: `make test`
- [x] Test count: **81** pass (68 + 13 new; exact N recorded on completion)

**Completion (2026-07-09)**: Added seam-B `deny_filter_stage()` for hardcoded amp ports, bogons,
and the slotted UDP blocked-port bitmap; global/service bands remain inactive until T4. RED check
failed on the first new amp-port assertion before wire-in, then gate `cd data-plane && make test`
→ **81 passed**; `make bpf` also rebuilt the normal object successfully.

**Tests**: dp-unit
**Gate**: quick

**Commit**: `feat(blacklist): amplification, bogon and blocked-port filters at seam B`

---

### T4: Global + service blacklist bands (bloom→LPM) + `bloom_hit_lpm_miss`

**What**: Steps 4–5 of the stage: global band (`gbl_meta[slot]` gate — missing entry →
`DR_MAP_ERROR`, `GBL_F_ACTIVE` skip, bloom peek unless `GBL_F_HAS_BROAD`, LPM confirm `{32,src}` →
`bl_state=GLOBAL_HIT` + `record_drop(DR_BLACKLIST_DROP)`); service band (`bl_flags & BL_F_ACTIVE`
gate, `sbl_*` scoped keys, `BL_F_HAS_BROAD`, hit → `SERVICE_HIT` + index 8); FP accounting:
`bump_bloom_fp(GLOBAL/SERVICE)` **only on bloom-actually-consulted** LPM misses + the deferred
whitelist bump (`bump_bloom_fp(WHITELIST)` in `whitelist_stage`'s consulted-miss path); test seed
helpers `seed_global_blacklist(cidr)` / `seed_service_blacklist(svc, cidr)` (bloom key + LPM entry
+ flag flips). New dp-unit cases: global hit drops for **two different services** (index 8),
service-scoped hit drops only for its service (cross-service passes to rules), clean miss reaches
the rule stage with `bl_state=CLEAN`, whitelist-over-blacklist precedence (same source whitelisted
on A + globally blacklisted → A redirects, B drops), overlap `/8`+`/32` any-coverage, global-before-
service attribution (`bl_state=GLOBAL_HIT` when both match), FP induction (bloom key without LPM
entry → verdict unchanged + `bloom_stats` reads exactly 1 in the right stage slot; confirmed hit
bumps nothing; whitelist FP bumps `WHITELIST` slot), `HAS_BROAD` escape (broad entry hits with
bloom skipped, **no** FP bump), fail-closed (`gbl_meta` entry deleted → `map_error`; LPM inner
removed → `map_error`).
**Where**: `data-plane/src/blacklist.h`, `data-plane/src/whitelist.h` (FP bump),
`data-plane/tests/test_parse.c`
**Depends on**: T3
**Reuses**: `wl_bloom_maybe`/`wl_lpm_hit` shapes (mirrored as `sbl_*`/`gbl_*`), `bump_sample_stat`
pattern, `seed_whitelist()` helper precedent
**Requirement**: BLK-02..07, BLK-17, BLK-18, BLK-20, BLK-22 (index 8 half), BLK-01 (band order
completes)

**Tools**: Skill `coding-guidelines`; MCP NONE

**Done when**:

- [x] All T3-level expectations still pass (empty blacklists ⇒ bands inactive ⇒ verdicts identical)
- [x] Scope + precedence + FP + escape + fail-closed cases above all pass
- [x] FP counter is **exact** (deterministic: N induced FPs read exactly N) and lives outside
      `counter_map` (drop-reason rows untouched — BLK-18/22)
- [x] Gate check passes: `make test`
- [x] Test count: **91** pass (T3's N + 10 new plus whitelist FP assertion; exact N recorded on completion)

**Completion (2026-07-09)**: Added global and service bloom→LPM bands plus exact per-stage
`bloom_stats` accounting, including WLV's deferred whitelist FP bump. RED check failed on the
whitelist FP assertion before implementation. Gate `cd data-plane && make test` → **91 passed**;
`make bpf` rebuilt the normal object successfully. Structural fail-closed coverage uses missing
global/service LPM inners; the `gbl_meta` lookup guard exists, but valid `ARRAY` slots are not
deletable by design.

**Tests**: dp-unit
**Gate**: quick

**Commit**: `feat(blacklist): scoped and global bloom-to-lpm blacklists with exact fp counter`

---

### T5: Loader pins + env-driven deny seed + live smoke (full gate)

**What**: Loader changes — pin `bloom_stats` under `/sys/fs/bpf/xdp_gateway/`; seed
`gbl_meta[0]={0}`; default seed leaves every deny map empty (BLK-23 baseline/smoke-neutral);
env-driven demo seed per design: `XDPGW_SEED_GBL_CIDR` (LPM + one /24 bloom key when prefix ≥ 24,
else `GBL_F_ACTIVE|GBL_F_HAS_BROAD`; always `GBL_F_ACTIVE`), `XDPGW_SEED_SBL_CIDR` (scoped entry on
the seeded service + `bl_flags` flip), `XDPGW_SEED_BLOCKED_PORT` (bit in slot 0) — seed documents
that the 16..23 expansion band is M4-builder behavior, not implemented here (D-BLK-2 simplification).
**Where**: `data-plane/loader/loader.c`
**Depends on**: T4
**Reuses**: `prepare_wl_seed`/env parsing idioms, pin lifecycle, `parse_service_dest`
**Requirement**: BLK-23; BLK-13 (seed-writer half, D-BLK-2); BLK-25 (native-attach half)

**Tools**: Skill `coding-guidelines`; MCP NONE

**Done when**:

- [x] `make bpf skel loader dpstat` green; loader attaches native/DRV fail-loud
- [x] Default (no env) behavior byte-identical: `sudo make smoke` passes unchanged
- [x] Env-seeded smoke: `XDPGW_SEED_GBL_CIDR=<smoke-src>/32 sudo make smoke` **fails to deliver**
      (blacklisted source dropped — inverse assertion documented in the smoke script or checked
      manually and recorded here on completion)
- [x] Manual verify documented here: seeded blocked port drops matching UDP; `active_slot` flip
      via seeded slots changes deny verdicts in one write
- [x] Gate check passes: `make test && sudo make smoke`
- [x] Test count: unchanged from T4 (**91** recorded)

**Completion (2026-07-09)**: Loader pins `bloom_stats`, seeds `gbl_meta[0]={0}`, accepts
`XDPGW_SEED_GBL_CIDR`, `XDPGW_SEED_SBL_CIDR`, and `XDPGW_SEED_BLOCKED_PORT`, and seeds the demo
service in both slots while deny demo data lands in slot 0. The live smoke source moved to
`45.45.0.1` so default smoke is not bogon-dropped. Gates:
`cd data-plane && make bpf skel loader dpstat && make test && sudo make smoke` → **91 passed** +
smoke delivered; `sudo env XDPGW_SEED_GBL_CIDR=45.45.0.1/32 make smoke` → no redirected frame
(expected); `sudo env XDPGW_SEED_BLOCKED_PORT=1234 make smoke` → no redirected frame (expected).

**Tests**: dp-integration (+ existing dp-unit suite)
**Gate**: full

**Commit**: `feat(blacklist): env-driven deny-list seed and bloom-stats pin in loader`

---

### T6: `dpstat` bloom-FP section

**What**: `dpstat counters` gains a `bloom_hit_lpm_miss` section — three per-stage rows
(whitelist / global-blacklist / service-blacklist) + total, summed across CPUs from the pinned
`bloom_stats`, printed after the existing sample-stats block; friendly gateway-not-loaded error
unchanged; drop-reason rows untouched (BLK-22's zero-change assertion for indices 4/7/8 verified
here by decoding live counts from T5's seeded runs).
**Where**: `data-plane/tools/dpstat.c`
**Depends on**: T5 (pin exists for live verification)
**Reuses**: `print_counters_once` per-CPU summation, `SAMPLE_STATS_PIN_PATH` pattern
**Requirement**: BLK-19; BLK-22 (zero-change verification half)

**Tools**: Skill `coding-guidelines`; MCP NONE

**Done when**:

- [x] `dpstat counters` without pins → unchanged friendly error; with a loaded gateway → bloom
      section prints (verified live: induce one FP via seeded bloom-only key, row reads 1)
- [x] `bogon_drop`/`udp_amplification_drop`/`blacklist_drop` rows decode with **zero** changes to
      the drop-reason table
- [x] Gate check passes: `make bpf skel loader dpstat && make test`
- [x] Test count: unchanged from T5 (**91** recorded)

**Completion (2026-07-09)**: `dpstat counters` now prints `bloom_hit_lpm_miss` rows for whitelist,
global blacklist, service blacklist, and total after sample stats. No-pins path remains the friendly
`gateway not loaded or map not pinned` error. Live loader check on a temporary veth showed the bloom
section with all rows; exact FP increments to 1 are covered by the T4 dp-unit cases for whitelist,
global, and service bloom-only keys. Gate `cd data-plane && make bpf skel loader dpstat && make test`
→ **91 passed**.

**Tests**: none (tools layer — build gate + manual verify per TESTING.md loader/tools convention)
**Gate**: build + quick (count-stability) + manual verify

**Commit**: `feat(blacklist): per-stage bloom false-positive section in dpstat counters`

---

### T7: Gated 1M bulk-load check (`make blbulk`) + footprint measurement

**What**: `tests/bulk_blacklist.c` + Makefile `blbulk` target (privileged, gated like `smoke`,
**never** part of `make test`): loads the skeleton, inserts **1M synthetic global entries** (mixed
/24–/32) into slot 0's LPM + pushes their bloom keys, hard-fails on any insert error (allocator
pressure = failed build per the M4 contract), records the memlock/RSS delta (**the** BLK-08
footprint documentation), verifies sample hit/miss lookups + one `BPF_PROG_TEST_RUN` verdict
against a loaded entry, and prints a summary (entries, bytes, ns/lookup spot check). Measured
footprint recorded here + fed to T8 docs.
**Where**: `data-plane/tests/bulk_blacklist.c` (new), `data-plane/Makefile`
**Depends on**: T5 (loader/skeleton conventions; privileged environment already exercised)
**Reuses**: skeleton open/load pattern from the test runner, `smoke` gating convention
**Requirement**: BLK-08 (measured half); BLK-25 (1M-load half)

**Tools**: Skill `coding-guidelines`; MCP NONE

**Done when**:

- [x] `sudo make blbulk` → 1M inserts succeed; footprint + spot-check numbers printed and recorded
      here on completion
- [x] Bloom + LPM agree on sampled members; non-members bloom-miss at plausible rate (sanity, not
      an exact FP assertion)
- [x] `make test` untouched by the new target (count unchanged from T6)
- [x] Gate check passes: `make bpf skel loader dpstat && make test && sudo make blbulk`

**Completion (2026-07-09)**: Added gated `make blbulk` (not part of `make test`) with
`tests/bulk_blacklist.c`. Full gate
`cd data-plane && make bpf skel loader dpstat && make test && sudo make blbulk` → **91 passed**,
then **1,048,576** LPM inserts and **1,048,576** bloom pushes succeeded. Gate-run footprint:
`cgroup_delta_kib=147364`, `rss_delta_kib=0`, deterministic key/value payload
`13631488` bytes. Spot checks: `/24`-backed LPM hit OK, `/32` LPM hit OK, LPM miss OK,
`avg_ns=1626.4` over 1,000 LPM lookups, bloom member hits `2`, nonmember misses `1024/1024`,
nonmember maybes `0`, and `BPF_PROG_TEST_RUN` for a loaded source returned `XDP_DROP`.

**Tests**: dp-integration (privileged, gated)
**Gate**: full (blbulk variant)

**Commit**: `test(blacklist): gated 1M bulk-load check with measured footprint`

---

### T8: Docs — TESTING.md conventions + README product notes

**What**: TESTING.md data-plane section gains deny-stage conventions (deny-map seed helpers,
FP-induction pattern, bogon-source rule for new tests — "packet sources must come from the named
non-bogon pools", `blbulk` gate, updated suite count from T4); `data-plane/README.md` gains the
verbatim D-BLK-1 amp-port set + A-BLK-1 bogon set, the resolver/NTP whitelisting onboarding
guidance ("a service that legitimately receives UDP from these source ports must whitelist those
upstream sources — whitelist requires an active VIP ceiling"), the D-BLK-2 seed-only bitmap
posture, and the measured 1M footprint from T7.
**Where**: `.specs/codebase/TESTING.md`, `data-plane/README.md`
**Depends on**: T7 (footprint number + final count exist)
**Reuses**: WLV T5 docs sections as templates
**Requirement**: BLK-26

**Tools**: NONE (docs only)

**Done when**:

- [ ] Both docs updated; TESTING.md dp-unit count matches T4's recorded N; README lists both sets
      verbatim + footprint
- [ ] Gate check passes: `make test` (docs-only; count unchanged)

**Tests**: none (docs; matrix has no doc layer)
**Gate**: quick (count-stability check only)

**Commit**: `docs(blacklist): deny-filter test conventions and amp-port/bogon product notes`

---

## Parallel Execution Map

```
Phase 1:
  ├── T1        (src contracts/maps; build+quick)
  └── T2 [P]    (tests-only migration; disjoint files — may run alongside T1)

Phase 2 (Sequential — shared blacklist.h/whitelist.h/test_parse.c):
  T1,T2 ──→ T3 ──→ T4

Phase 3 (Sequential — privileged gates not parallel-safe; T8 needs T7's number):
  T4 ──→ T5 ──→ T6 ──→ T7 ──→ T8
```

Only **T2** carries `[P]`: dp-unit is parallel-safe as infrastructure and T1/T2 touch disjoint
files with no behavior change on either side. T5/T7 run privileged dp-integration gates
(Parallel-Safe: **No** per TESTING.md); T6 needs T5's pin for live verification; T8 needs T7's
measured footprint.

---

## Pre-Approval Validation

### Check 1: Task Granularity

| Task | Scope | Status |
| --- | --- | --- |
| T1 | 1 new contract header + 2 pad-byte fields + include | ✅ Granular (cohesive contract unit — ARL/WLV T1 precedent) |
| T2 | 1 mechanical test-source sweep (2 test files) | ✅ Granular (single concern, verdict-neutral) |
| T3 | 1 stage function (3 filter steps) + 1 rewire + its tests | ✅ Granular |
| T4 | 2 lookup bands in same stage + 1 counter + its tests | ✅ Granular (cohesive: the bloom→LPM machinery) |
| T5 | 1 file (loader) + smoke | ✅ Granular |
| T6 | 1 file (dpstat section) | ✅ Granular |
| T7 | 1 new gated check + 1 Makefile target | ✅ Granular |
| T8 | 2 doc files | ✅ Granular |

### Check 2: Diagram–Definition Cross-Check

| Task | Depends On (body) | Diagram shows | Status |
| --- | --- | --- | --- |
| T1 | None | Phase-1 start | ✅ Match |
| T2 | None | Phase-1 start, [P] lane | ✅ Match |
| T3 | T1, T2 | T1,T2 → T3 | ✅ Match |
| T4 | T3 | T3 → T4 | ✅ Match |
| T5 | T4 | T4 → T5 | ✅ Match |
| T6 | T5 | T5 → T6 | ✅ Match |
| T7 | T5 | T5 → … → T7 | ⚠→✅ body says T5; chain shows T6 → T7 — T7 also *sequences after* T6 because both need the privileged runner and T6's live verify uses T5's seeded runs; dependency is T5, ordering is the phase lane. No parallel conflict (neither is [P]) |
| T8 | T7 | T7 → T8 | ✅ Match |

T1/T2 are the only same-phase pair; neither depends on the other, disjoint files. ✅

### Check 3: Test Co-location Validation

| Task | Code layer created/modified | Matrix requires | Task says | Status |
| --- | --- | --- | --- | --- |
| T1 | Data-plane contracts/maps (dp-unit layer, load proof) | dp-unit | dp-unit (load via suite) | ✅ OK |
| T2 | dp-unit suite itself (migration) | dp-unit | dp-unit | ✅ OK |
| T3 | XDP verdict stage | dp-unit | dp-unit | ✅ OK |
| T4 | XDP verdict stage + runtime counter | dp-unit | dp-unit | ✅ OK |
| T5 | Native loader + redirect path | dp-integration (smoke) | dp-integration | ✅ OK |
| T6 | Tools (dpstat) — build gate + manual verify per TESTING.md | none (tools) | none + manual | ✅ OK |
| T7 | Privileged scale check (new gated lane) | dp-integration | dp-integration (gated) | ✅ OK |
| T8 | Docs only | none | none | ✅ OK |

---

## Requirement Coverage

| Requirement | Task(s) | | Requirement | Task(s) |
| --- | --- | --- | --- | --- |
| BLK-01 | T3+T4 | | BLK-14 | T3 |
| BLK-02 | T4 | | BLK-15 | T3 |
| BLK-03 | T4 | | BLK-16 | T3 |
| BLK-04 | T4 | | BLK-17 | T4 |
| BLK-05 | T4 | | BLK-18 | T4 |
| BLK-06 | T4 | | BLK-19 | T6 |
| BLK-07 | T3+T4 | | BLK-20 | T4 |
| BLK-08 | T1+T7 | | BLK-21 | T1 |
| BLK-09 | T1+T3+T4 | | BLK-22 | T3+T4+T6 |
| BLK-10 | T3 | | BLK-23 | T5 |
| BLK-11 | T3 | | BLK-24 | T2+T3 |
| BLK-12 | T3 | | BLK-25 | T1+T5+T7 |
| BLK-13 | T3+T5 | | BLK-26 | T8 |

**Coverage:** 26/26 requirements mapped, 0 unmapped.
