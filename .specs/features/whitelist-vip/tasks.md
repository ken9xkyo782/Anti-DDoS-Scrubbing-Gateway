# Whitelist/VIP (Scoped) & VIP Ceiling Tasks

**Design**: `.specs/features/whitelist-vip/design.md` (AD-021)
**Status**: Approved (2026-07-09) → Execute
**Baseline**: dp-unit suite **B = 50** (post-ARL, verified 2026-07-09: `make test` → 50 passed).
ARL is Executed/VERIFIED, so the A-WLV-8 execute gate is **satisfied**.
**Tools (per STATE Preferences)**: Skill `coding-guidelines` on all C/XDP code tasks (T1–T4); no MCPs
configured. T5 uses Skill `docs-writer` conventions only if trivial; otherwise none.

---

## Execution Plan

### Phase 1: Contracts & de-risk (Sequential)

```
T1
```

### Phase 2: Behavior (Sequential — shared files)

```
T1 → T2 → T3
```

### Phase 3: Loader & live smoke + docs

```
T3 → T4          (full gate; smoke NOT parallel-safe)
T3 → T5 [P]      (docs-only; may run alongside T4)
```

T1–T3 serialize: they all edit `src/whitelist.h` and `tests/test_parse.c`. T4 runs the privileged
smoke (dp-integration, not parallel-safe). T5 touches only `TESTING.md`/`README.md` and may run in
parallel with T4.

---

## Task Breakdown

### T1: Contract headers, maps & bloom-composition de-risk

**What**: New `src/whitelist.h` with all structs/enums/constants (`wl_lpm_key`, `wl_bloom_key`,
`vip_config`, `wl_service_flags`, `vip_flags`, sizes per design Data Models) and all 7 map
definitions (`whitelist_bloom_0/1` + outer, `whitelist_lpm_0/1` + outer, `vip_config_0/1` + outer,
`vip_ceiling_state`); `service_val` gains `wl_flags` (pad byte, size stays 8); `pkt_meta` gains
`wl_state` (pad byte, size stays 32); header included from `xdp_gateway.bpf.c` (maps emitted, stage
not yet wired). **De-risk (ladder rung selection, fail-fast)**: program loads with static
`BLOOM_FILTER` inners declared in `__array(values,…)` + `map_extra`; one new dp-unit case pushes a
`wl_bloom_key` from userspace via the skeleton and asserts peek/lookup(NULL-key) semantics (0 =
maybe, -ENOENT = definitely absent). If static inners fail to load → fall back to loader-created
inners (rung 2) and record the rung in this file; LPM-only (rung 3) only if both fail.
**Where**: `data-plane/src/whitelist.h` (new), `data-plane/src/service.h`,
`data-plane/src/pkt_meta.h`, `data-plane/src/xdp_gateway.bpf.c` (include only),
`data-plane/tests/test_parse.c` (de-risk case)
**Depends on**: None
**Reuses**: `rules.h` map-declaration shape (`ARRAY_OF_MAPS`[2] + static inners), `service_map`
LPM-inner precedent (SLRD), `_Static_assert` size-pinning convention
**Requirement**: WLV-19; WLV-23 (composition half); WLV-04 (bloom semantics half); WLV-09 (field)

**Tools**: Skill `coding-guidelines`; MCP NONE

**Done when**:

- [x] `whitelist.h` matches the design Data Models field-for-field (incl. bytes/sec `bps` note,
      M4 contract comments: bloom replace-only + meta-equal params, superset-of-LPM invariant,
      D-WLV-1 builder rules)
- [x] `sizeof(struct service_val) == 8` and `sizeof(struct pkt_meta) == 32` static-asserted
      (existing asserts still pass)
- [x] `sizeof(struct vip_config) == 24` and `sizeof(struct wl_bloom_key) == 8` static-asserted
- [x] Program **loads** with all whitelist maps present (bloom-as-static-inner proven or fallback
      rung documented here + in STATE)
- [x] De-risk dp-unit case: userspace push → lookup round-trip on a slot-selected bloom inner
      (definitely-absent vs maybe-present asserted)
- [x] Gate check passes: `make bpf skel loader dpstat && make test`
- [x] Test count: **51** pass (50 baseline unchanged + 1 de-risk case)

**Completion (2026-07-09)**: primary rung proven — static `BLOOM_FILTER` inners inside
`ARRAY_OF_MAPS` loaded, XDP `bpf_map_peek_elem` on the slot-selected inner passed, and
`cd data-plane && make bpf skel loader dpstat && make test` → **51 passed**.

**Tests**: dp-unit
**Gate**: build + quick

**Commit**: `feat(whitelist): add scoped whitelist/vip map contracts and bloom de-risk`

---

### T2: Scoped match stage + wire-in (flag gate, bloom→LPM, miss path)

**What**: `whitelist_stage(ctx, meta, slot, wl_flags)` in `whitelist.h` implementing: `WL_F_ACTIVE`
gate (D-WLV-1 inactive = clean miss), `WL_F_HAS_BROAD` bloom skip, bloom peek (miss = definitive
negative), composite-key LPM confirm, `vip_config` lookup on hit (entry with neither SET flag →
miss; missing entry → `DR_MAP_ERROR`), hit → set `wl_state=HIT_ADMIT` → `redirect_out()` directly
(**not** `admit_clean()` — ceiling bucket lands in T3, hit admits unconditionally until then), miss
→ `wl_state=MISS` → `allow_rule_stage()`; fail-closed `DR_MAP_ERROR` on outer/inner map absence;
both **marked seams** (A: ingress-cap comment above the call in `service_lookup_redirect`; B: M3#3
comment on the miss path); enabled branch of `service_lookup_redirect` rewired to call the stage;
`seed_whitelist(svc, cidr, vip_cfg)` test helper (bloom keys + LPM entry + vip_config + wl_flags
flip in `service_map`).
**Where**: `data-plane/src/whitelist.h`, `data-plane/src/xdp_gateway.bpf.c`,
`data-plane/tests/test_parse.c`
**Depends on**: T1
**Reuses**: `record_drop()`, `redirect_out()`/`test_meta_map`, pinned-slot discipline (never
re-read `active_config`), `seed_rule_block()` helper pattern
**Requirement**: WLV-01..09, WLV-13 (inactive gate), WLV-22 (quick-suite half), WLV-23 (verifier
half: peek from XDP), WLV-24

**Tools**: Skill `coding-guidelines`; MCP NONE

**Done when**:

- [x] Baseline 50 cases pass **unmodified** (no whitelist seeded → miss path; spot-check
      `wl_state == 0` on one enabled-service case) — WLV-22
- [x] Scope isolation: whitelist `198.51.100.0/24` on svc A only → in-range src to A redirects with
      **no rule block seeded** (`wl_state=2`, `rule_idx=0xFF`); same src to svc B → `not_allowed`
      (`wl_state=1`); out-of-range src to A → rule path (WLV-02/03/05/06)
- [x] Bloom guard: forced FP (bloom key without LPM entry) → clean miss, verdict identical;
      `/16` entry + `WL_F_HAS_BROAD` → hit without bloomed keys (WLV-04, A-WLV-1)
- [x] D-WLV-1: entries seeded but `WL_F_ACTIVE` unset → clean miss; `vip_config` with neither SET
      flag → clean miss (WLV-13 gate half)
- [x] Fail-closed: `WL_F_ACTIVE` set + whitelist LPM inner removed from outer → `map_error`;
      LPM hit + `vip_config` entry deleted → `map_error` (WLV-07)
- [x] Pipeline neighbors: disabled svc + whitelist → `service_disabled`; ARP still redirects;
      GRE from whitelisted src redirects (bypass is protocol-blind)
- [x] Program still loads native (bounded stage + `bpf_map_peek_elem` on bloom pass the verifier —
      WLV-23 verifier half)
- [x] Gate check passes: `make test`
- [x] Test count: **62** pass (51 + 11 new; exact N recorded on completion)

**Completion (2026-07-09)**: `cd data-plane && make test` → **62 passed**; production build
`cd data-plane && make bpf skel loader dpstat` also passed.

**Tests**: dp-unit
**Gate**: quick

**Commit**: `feat(whitelist): scoped bloom-to-lpm whitelist stage with fail-closed miss semantics`

---

### T3: VIP ceiling bucket (aggregate per-service, terminal index 14)

**What**: `vip_bucket_admit(cfg, meta, pkt_len)` + thin `vip_bucket_{reset,refill,consume}` wrappers
in `whitelist.h` reusing `struct rl_bucket`, `rl_burst`/`rl_refill_dim`/`rl_cpu_count`/
`rl_test_no_refill` verbatim (**`rules.h` untouched**); wired between the T2 hit and
`redirect_out()`: admit iff every SET dim has tokens (NULL dim unlimited; `0`+SET = block), drop
consumes neither dim, exhausted → `wl_state=HIT_DROP` + `record_drop(DR_VIP_CEILING_DROP)` (frozen
index 14, terminal); lazy version-reset vs `vip_config.version`; bucket-insert failure →
`DR_MAP_ERROR`.
**Where**: `data-plane/src/whitelist.h`, `data-plane/tests/test_parse.c`
**Depends on**: T2
**Reuses**: ARL bucket algebra + shared `rl_config.test_no_refill` knob + CPU-pinned runner +
`rl_ncpus` rodata (all existing)
**Requirement**: WLV-10..17 (13: `0=block`/one-dim halves), WLV-18, WLV-20

**Tools**: Skill `coding-guidelines`; MCP NONE

**Done when**:

- [x] Deterministic ceiling: `test_no_refill=1`, `pps=3` → exactly 3 redirects then drops; counter
      index 14 exact; seeded match-all rule receives **none** of the overflow (terminal, WLV-11);
      `dpstat` decode needs zero changes (WLV-20)
- [x] One-dim + zero: `pps` set / `bps` NULL → bps unlimited; `pps=0`+`PPS_SET` → every whitelisted
      packet drops index 14 (WLV-13)
- [x] Dim independence: pps-exhausted drop leaves `bps_tokens` untouched (WLV-14)
- [x] Aggregate sharing: two distinct whitelisted sources drain one budget (burst 5 → 5 total
      admits across both, order-independent) (WLV-12)
- [x] Reset-on-swap: exhaust → rewrite `vip_config` with `version+1` → next packet admits (WLV-15)
- [x] Normal mode: first packet on a fresh bucket admits (init path); VIP admit still calls
      `redirect_out()` directly, never `admit_clean()` (WLV-18, code-level assert via review +
      overflow-isolation case above)
- [x] Gate check passes: `make test`
- [x] Test count: **68** pass (T2's 62 + 6 new; exact N recorded on completion)

**Completion (2026-07-09)**: `cd data-plane && make test` → **68 passed**; production build
`cd data-plane && make bpf skel loader dpstat` also passed.

**Tests**: dp-unit
**Gate**: quick

**Commit**: `feat(whitelist): aggregate per-service vip ceiling with terminal vip_ceiling_drop`

---

### T4: Loader env-driven VIP seed + live smoke (full gate)

**What**: Loader seed extension — default seed **unchanged** (whitelist-free); when
`XDPGW_SEED_WL_CIDR` is set (with optional `XDPGW_SEED_VIP_PPS`/`XDPGW_SEED_VIP_BPS`, default
pps=1000 if neither given so ACTIVE is never emitted ceiling-less), seed both slots' bloom keys +
LPM entry + `vip_config{version=1}` and set `WL_F_ACTIVE` (+`WL_F_HAS_BROAD` when cidr < /24) on
the seeded service — demoable without M4 (D-SLRD-1 posture). If T1 landed on de-risk rung 2,
the loader also creates/attaches the bloom inners here.
**Where**: `data-plane/loader/loader.c`
**Depends on**: T3
**Reuses**: existing seed helpers + env-var conventions (`SERVICE_DEST` precedent), pin/detach
lifecycle (unchanged)
**Requirement**: WLV-21; WLV-22 (smoke half); WLV-23 (native-attach half); WLV-18 (headers via
smoke TTL/csum assertions, inherited)

**Tools**: Skill `coding-guidelines`; MCP NONE

**Done when**:

- [ ] `make bpf skel loader dpstat` green; loader attaches native/DRV fail-loud
- [ ] Default (no env) seed byte-identical behavior: `sudo make smoke` passes unchanged
      (TTL/checksum assertions green — WLV-22/18)
- [ ] Manual verify documented in this file: `XDPGW_SEED_WL_CIDR=198.51.100.0/24 SERVICE_DEST=…
      sudo ./build/xdp_gateway_loader IN OUT` → whitelisted src forwards with zero rules;
      `dpstat counters` shows `vip_ceiling_drop` counting when `XDPGW_SEED_VIP_PPS=1` under load
- [ ] Ceiling-less ACTIVE state impossible via seed (D-WLV-1 honored by the interim writer too)
- [ ] Gate check passes: `make test && sudo make smoke`
- [ ] Test count: T3's N pass (unchanged)

**Tests**: dp-integration (+ existing dp-unit suite)
**Gate**: full

**Commit**: `feat(whitelist): env-driven whitelist/vip seed in loader with smoke-neutral default`

---

### T5: Docs — TESTING.md conventions + README product notes [P]

**What**: TESTING.md data-plane section gains whitelist-stage conventions (`seed_whitelist()`,
shared `test_no_refill` knob covering rule **and** VIP buckets, bloom de-risk pattern, updated
suite count from T3); `data-plane/README.md` gains the D-WLV-1 onboarding note ("whitelist requires
a VIP ceiling to take effect"; NULL/NULL = inert whitelist) + the BL-08 residual failure mode
(spoofed VIP flood → `vip_ceiling_drop` self-DoS, bounded by design; alert = M6) + bloom
replace-only M4 note.
**Where**: `.specs/codebase/TESTING.md`, `data-plane/README.md`
**Depends on**: T3 (conventions + final count exist)
**Reuses**: existing rule-stage conventions section as the template
**Requirement**: WLV-25

**Tools**: NONE (docs only)

**Done when**:

- [ ] Both docs updated; TESTING.md dp-unit count matches T3's recorded N
- [ ] Gate check passes: `make test` (docs-only; count unchanged)
- [ ] Test count: T3's N pass

**Tests**: none (docs; matrix has no doc layer)
**Gate**: quick (count-stability check only)

**Commit**: `docs(whitelist): whitelist-stage test conventions and vip-ceiling product notes`

---

## Parallel Execution Map

```
Phase 1–2 (Sequential — shared src/whitelist.h + tests/test_parse.c):
  T1 ──→ T2 ──→ T3

Phase 3 (after T3):
  ├── T4        (sequential lane: privileged smoke, NOT parallel-safe)
  └── T5 [P]    (docs-only; disjoint files — may run alongside T4)
```

Only **T5** carries `[P]`: dp-unit is parallel-safe as infrastructure, but T1–T3 edit the same two
files (serialize); T4 runs `sudo make smoke` (dp-integration, Parallel-Safe: **No** per TESTING.md).
T5 edits only docs and shares no files with T4.

---

## Pre-Approval Validation

### Check 1: Task Granularity

| Task | Scope | Status |
| --- | --- | --- |
| T1 | 1 new header (contracts+maps) + 2 pad-byte fields + 1 de-risk case | ✅ Granular (cohesive contract unit, ARL-T1 precedent) |
| T2 | 1 stage function + 1 call-site rewire + its tests | ✅ Granular |
| T3 | 1 bucket function family in same header + its tests | ✅ Granular |
| T4 | 1 file (loader seed) + smoke run | ✅ Granular |
| T5 | 2 doc files | ✅ Granular |

### Check 2: Diagram–Definition Cross-Check

| Task | Depends On (body) | Diagram shows | Status |
| --- | --- | --- | --- |
| T1 | None | start of chain | ✅ Match |
| T2 | T1 | T1 → T2 | ✅ Match |
| T3 | T2 | T2 → T3 | ✅ Match |
| T4 | T3 | T3 → T4 | ✅ Match |
| T5 | T3 | T3 → T5 [P] | ✅ Match |

T4 and T5 are the only same-phase pair; neither depends on the other and they share no files. ✅

### Check 3: Test Co-location Validation

| Task | Code layer created/modified | Matrix requires | Task says | Status |
| --- | --- | --- | --- | --- |
| T1 | Data-plane contracts/maps (dp-unit layer) | dp-unit | dp-unit (de-risk case in-task) | ✅ OK |
| T2 | XDP verdict stage (dp-unit layer) | dp-unit | dp-unit | ✅ OK |
| T3 | XDP verdict stage (dp-unit layer) | dp-unit | dp-unit | ✅ OK |
| T4 | Native loader (build gate + manual smoke per TESTING.md) + redirect path | dp-integration | dp-integration | ✅ OK |
| T5 | Docs only (no code layer in matrix) | none | none | ✅ OK |

---

## Requirement Coverage

| Requirement | Task(s) | | Requirement | Task(s) |
| --- | --- | --- | --- | --- |
| WLV-01 | T2 | | WLV-14 | T3 |
| WLV-02 | T2 | | WLV-15 | T3 |
| WLV-03 | T2 | | WLV-16 | T3 (bound doc’d T5) |
| WLV-04 | T1+T2 | | WLV-17 | T3 |
| WLV-05 | T2 | | WLV-18 | T3+T4 |
| WLV-06 | T2 | | WLV-19 | T1 |
| WLV-07 | T2 | | WLV-20 | T3 |
| WLV-08 | T2 (structural) | | WLV-21 | T4 |
| WLV-09 | T1+T2 | | WLV-22 | T2+T4 |
| WLV-10 | T3 | | WLV-23 | T1+T2+T4 |
| WLV-11 | T3 | | WLV-24 | T2 |
| WLV-12 | T3 | | WLV-25 | T5 |
| WLV-13 | T2+T3 | | | |

**Coverage:** 25/25 requirements mapped, 0 unmapped.
