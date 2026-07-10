# Fairness & Bandwidth Reservation (§8.4) — Tasks

**Design**: `.specs/features/fairness-bandwidth/design.md` (AD-025, approved 2026-07-10)
**Spec**: `spec.md` (FAIR-01..27) · **Context**: `context.md` (D-FAIR-1..2)
**Status**: Approved (2026-07-10)

**Baseline (pinned live 2026-07-10):** blacklist-filters is **executed** (commits `692ebf5..1421daa`);
`cd data-plane && make test` → **B = 91 passed**. A-FAIR-1's execute gate is satisfied.
Gates run from `data-plane/`: **build** = `make bpf skel loader dpstat` · **quick** = `make test` ·
**full** = `make test && sudo make smoke` (privileged, not parallel-safe).

**Tooling (per STATE Preferences):** Skill `coding-guidelines` on all C/XDP code tasks; no MCPs.

---

## Execution Plan

```
Phase 1 (sequential — shared src/fairness.h + tests/test_parse.c):
  T1 ──→ T2 ──→ T3 ──→ T4
Phase 2 (after T4):
  T5 (privileged full gate — not parallel-safe)
  T6 [P] (docs only — may run alongside T5)
```

---

## Task Breakdown

### T1: Fairness contracts, maps & spin-lock de-risk

**What**: Create `src/fairness.h` with the full map contract (no stage logic yet) and prove the
one novel kernel composition — a BTF `bpf_spin_lock` value in a top-level HASH — loads under
native/DRV, selecting the FAIR-22 rung fail-fast; grow `pkt_meta` 32→40.
**Where**: `data-plane/src/fairness.h` (new), `data-plane/src/pkt_meta.h`,
`data-plane/tests/test_parse.c` (+1 de-risk case), `data-plane/src/xdp_gateway.bpf.c`
(include only, so the maps/BTF enter the object)
**Depends on**: None
**Reuses**: `rl_bucket`/`rl_config` shapes (`rules.h`), `ARRAY_OF_MAPS`[2]-of-HASH composition
(`rule_block_map`/`vip_config_map`), slot-keyed `ARRAY[2]` (`gbl_meta` precedent)
**Requirement**: FAIR-20, FAIR-22, FAIR-08 (field), A-FAIR-2/8

**Done when**:
- [x] `struct fair_config` (40 B), `struct fair_node_config` (16 B),
      `struct fair_committed_bucket` (lock top-level, one per value) + static asserts;
      `FAIR_CONFIG_MAX_ENTRIES = 1024`, `FAIR_RATE_MAX = 16000000000ULL`, `fair_state` enum
      (`FAIR_NONE..FAIR_ERR`)
- [x] 6 maps defined: `fair_config_map` (AoM[2]/HASH), `fair_node_config` (ARRAY[2]),
      `svc_committed_state` (HASH+lock, prealloc), `svc_burst_state` (PERCPU_HASH),
      `node_burst_state` (PERCPU_ARRAY[1]), `service_ingress_cap_state` (PERCPU_HASH)
- [x] `pkt_meta` grows to 40 B (`fair_state` + `_pad2[7]`), assert migrated — deliberate growth
      documented in a header comment
- [x] De-risk dp-unit case: test hook takes/releases the committed-bucket lock and mutates
      tokens (pure-ALU CS, `now` captured outside); **rung selection recorded** — if the verifier
      rejects, stop and document the fallback rung before proceeding (FAIR-22)
- [x] Gate: `make bpf skel loader dpstat` passes; `make test` → **92** (B+1, zero baseline churn)

**Completion (2026-07-10)**: Primary BTF HASH + `bpf_spin_lock` rung loaded successfully. The
dp-unit hook acquired/released the lock and incremented the committed token counter; no fallback
rung was needed. `cd data-plane && make bpf skel loader dpstat && make test` → **92 passed**.

**Tests**: dp-unit (de-risk case) · **Gate**: build + quick
**Commit**: `feat(fairness): map contracts, pkt_meta growth and spin-lock de-risk`

---

### T2: Ingress-cost cap stage at seam A

**What**: Implement `fair_cap_admit()` + `ingress_cap_stage()` (dual pps+bps per-CPU bucket,
lazy version reset) and wire it into `service_lookup_redirect` at WLV-24 seam A; seed the test
harness with generous `fair_config` rows so the 92-case baseline keeps every verdict.
**Where**: `data-plane/src/fairness.h`, `data-plane/src/xdp_gateway.bpf.c` (seam A),
`data-plane/tests/test_parse.c` (harness fair seed + new cap cases)
**Depends on**: T1
**Reuses**: `rl_burst`/`rl_refill_dim`/`rl_bucket_consume` verbatim, `rl_test_no_refill`,
`rl_cpu_count`, D-ARL-2 lazy-reset pattern, `record_drop`
**Requirement**: FAIR-14, FAIR-15, FAIR-16, FAIR-17, FAIR-18, FAIR-19, FAIR-21 (idx 13)

**Done when**:
- [x] Every post-service-match packet draws cap tokens regardless of eventual verdict; over-cap
      → `record_drop(DR_INGRESS_CAP_DROP)` (idx 13) with **no** whitelist/deny/rule work
      (`wl_state/bl_state/rule_idx` all NONE, `fair_state = FAIR_CAP_DROP`)
- [x] Both dimensions enforced (pps and bps exhaust independently); key = service_id only;
      missing `fair_config` row for a matched enabled service → `DR_MAP_ERROR`
- [x] Harness seeds generous budgets for all test services — baseline verdicts unchanged
- [x] New cases: under-cap continue; over-cap pps-dim; over-cap bps-dim; stage-progression
      assert; VIP-source-over-cap drops at 13 (FAIR-18 precedence); missing-row map_error;
      version-flip resets the cap bucket
- [x] Gate: `make test` → **≥ 99** (92 baseline intact + ≥7 new)

**Completion (2026-07-10)**: The destination-keyed dual PPS/BPS ingress cap now runs before the
whitelist stage. New deterministic cases cover both dimensions, VIP precedence, stage progression,
missing config, and version reset. `cd data-plane && make test` → **99 passed**.

**Tests**: dp-unit · **Gate**: quick
**Commit**: `feat(fairness): destination-keyed dual-dimension ingress-cost cap at seam A`

---

### T3: Committed/burst/node admit ladder at admit_clean()

**What**: Implement `fair_committed_admit()` (spin-locked exact CS), `fair_burst_admit()`,
`fair_node_admit()` and `fair_admit_stage()`; rewire `admit_clean(ctx, meta, slot)` to the
ladder; wire idx 11/12.
**Where**: `data-plane/src/fairness.h`, `data-plane/src/rules.h` (`admit_clean` signature +
body only), `data-plane/tests/test_parse.c`
**Depends on**: T2
**Reuses**: same `rl_*` helpers + `test_no_refill` + CPU-pinned runner conventions (AD-019);
`redirect_out()`; kernel-lazy element creation (`rl_bucket_admit` pattern, `BPF_NOEXIST`)
**Requirement**: FAIR-01, FAIR-02, FAIR-03, FAIR-04, FAIR-06, FAIR-07, FAIR-08, FAIR-09,
FAIR-10, FAIR-11, FAIR-13, FAIR-21 (idx 11/12), FAIR-25

**Done when**:
- [ ] Ladder order per design: committed (exact, `now` before lock, version-check/refill/consume
      inside pure-ALU CS) → admit skips burst+node; else burst (rate÷nCPU) consuming
      service-then-node, no refund; else drops 11 (burst empty) / 12 (node empty);
      `fair_state` records the outcome; VIP path untouched (never enters)
- [ ] `fair_node_config[slot]` supplies headroom; missing/structural failures → `DR_MAP_ERROR`;
      all four buckets honor `test_no_refill` and lazy version reset
- [ ] New deterministic cases: committed exact admit count (no pinning needed — global bucket);
      burst path admits + node dual-draw; ceiling drop 11; congestion drop 12 (burst tokens
      remain, node empty); committed=0 all-burst; committed=ceiling zero-burst; version-flip
      re-grants burst once; node headroom 0 sheds all burst (FAIR-13)
- [ ] Gate: `make test` → **≥ 107** (T2 count intact + ≥8 new)

**Tests**: dp-unit · **Gate**: quick
**Commit**: `feat(fairness): exact committed bucket with burst/node admit ladder`

---

### T4: Deterministic fairness scenario — the M3 milestone gate

**What**: The FAIR-24 dp-unit scenario: service A flooded past cap and ceiling with small
packets (pps dimension per D-FAIR-1) while interleaved service-B committed traffic admits 100%;
reason attribution asserted at each exhaustion point (13 → 11 → 12).
**Where**: `data-plane/tests/test_parse.c`
**Depends on**: T3
**Reuses**: T2/T3 harness seeding, `test_no_refill` determinism, counter assertions
**Requirement**: FAIR-05, FAIR-24 (dp-unit half), FAIR-26

**Done when**:
- [ ] Scenario: A seeded tight (small committed/burst/cap), B seeded normal; interleaved
      A-flood/B-committed frames → **every** B packet admits `FAIR_COMMITTED`; A shows
      `ingress_cap_drop`, `service_ceiling_drop`, `congestion_drop` at the seeded exhaustion
      points with exact counter deltas
- [ ] A second pass with A's flood removed proves B's admit count identical (flood changed
      nothing for B — CM-04 stated as an assertion)
- [ ] Full suite green: `make test` → **≥ 110** (T3 count intact + ≥3 scenario cases)

**Tests**: dp-unit · **Gate**: quick
**Commit**: `test(fairness): deterministic fairness scenario proving committed isolation`

---

### T5: Loader env seed, default posture & fairness smoke

**What**: Extend the loader/seed with the FAIR env family (budgets precomputed userspace-side,
D-FAIR-1/2), default-generous posture (FAIR-23), and the gated live-smoke fairness variant.
**Where**: `data-plane/loader/loader.c`, `data-plane/Makefile` + smoke script (fairness variant)
**Depends on**: T4
**Reuses**: `parse_u64_env`/`parse_u16_env`, existing seed structure, smoke harness (SLRD/ARL)
**Requirement**: FAIR-12, FAIR-23, FAIR-24 (smoke half), FAIR-21 (dpstat rows live), FAIR-26

**Done when**:
- [ ] Env family: `XDPGW_FAIR_COMMITTED_BPS`, `XDPGW_FAIR_CEILING_BPS`,
      `XDPGW_NODE_CLEAN_CAPACITY_BPS` (default 5e9 = 40 Gbps), `XDPGW_FAIR_K` (default 3),
      `XDPGW_FAIR_REF_PKT` (default 512); seed computes burst/cap_bps/cap_pps/headroom
      (floor 0) with the `FAIR_RATE_MAX` clamp; writes `fair_config` rows for every seeded
      service + `fair_node_config` per slot
- [ ] Default (no env) = committed=ceiling=100 Gbps → baseline smoke behavior unchanged
- [ ] Fairness smoke variant (gated with `make smoke`): constrained seed on the veth path shows
      redirect-then-drop transition and live `dpstat counters` rows at indices 13/11/12
      (zero dpstat code change — FAIR-21 verified here)
- [ ] Gate (full): `make test && sudo make smoke` green at the T4 count

**Tests**: dp-integration · **Gate**: full (privileged, **not parallel-safe**)
**Commit**: `feat(fairness): env-driven plan seed with node capacity defaults and fairness smoke`

---

### T6: Docs — TESTING.md conventions + README fairness section [P]

**What**: FAIR-27 collateral: data-plane TESTING.md fairness conventions (deterministic ladder
testing, harness seeding, scenario shape) and README/product notes (ladder semantics, env knobs
k/ref-pkt/capacity with defaults, VIP-under-cap precedence, §8.4.6 residual limitation, §8.3
name mapping, `FAIR_RATE_MAX` clamp).
**Where**: `.specs/codebase/TESTING.md`, `data-plane/README.md`
**Depends on**: T4 (conventions final; env names design-fixed)
**Reuses**: existing TESTING.md data-plane section structure (WLV/BLK precedent)
**Requirement**: FAIR-27, FAIR-18 (documented precedence), FAIR-13 (documented consequence)

**Done when**:
- [ ] TESTING.md: fairness seeding + determinism conventions (committed exact without pinning;
      burst/node/cap pinned), scenario documentation
- [ ] README: ladder semantics (committed exact / burst per-CPU / node shared), env knob table
      with defaults, VIP-under-cap note, §8.4.6 residual limitation verbatim in spirit,
      oversubscription consequence (headroom 0)
- [ ] Gate: build (docs-only change; `make bpf` still passes)

**Tests**: none (docs) · **Gate**: build
**Commit**: `docs(fairness): ladder conventions, node-config knobs and residual-limit notes`

---

## Pre-Approval Validation

### Check 1 — Granularity

| Task | Scope | Status |
| --- | --- | --- |
| T1 | 1 new header (contracts+maps) + 1 struct growth + 1 de-risk case | ✅ cohesive foundation unit (ARL/WLV/BLK T1 precedent) |
| T2 | 1 stage + 1 seam wire + its co-located tests | ✅ |
| T3 | 1 stage (3 bucket helpers, one ladder) + 1 seam rewire + tests | ✅ cohesive — the ladder is one function |
| T4 | 1 test scenario | ✅ |
| T5 | 1 loader extension + 1 smoke variant | ✅ (seed and its smoke proof are one deliverable) |
| T6 | 2 doc files | ✅ |

### Check 2 — Diagram-Definition Cross-Check

| Task | Depends on (body) | Diagram shows | Status |
| --- | --- | --- | --- |
| T1 | None | entry | ✅ |
| T2 | T1 | T1→T2 | ✅ |
| T3 | T2 | T2→T3 | ✅ |
| T4 | T3 | T3→T4 | ✅ |
| T5 | T4 | T4→T5 | ✅ |
| T6 | T4 | T4→T6 `[P]` | ✅ (parallel with T5 only; no shared files, no tests) |

### Check 3 — Test Co-location (vs TESTING.md matrix)

| Task | Layer modified | Matrix requires | Task says | Status |
| --- | --- | --- | --- | --- |
| T1 | XDP maps/contracts (verdict-neutral) | dp-unit | dp-unit (de-risk) | ✅ |
| T2 | XDP verdict stage | dp-unit | dp-unit | ✅ |
| T3 | XDP verdict stage | dp-unit | dp-unit | ✅ |
| T4 | dp-unit suite | dp-unit | dp-unit | ✅ |
| T5 | loader + live redirect path | dp-integration | dp-integration | ✅ |
| T6 | docs only | none | none | ✅ |

**Parallelism:** only T6 is `[P]` — T1→T4 serialize on `fairness.h`/`test_parse.c`; T5's smoke
is privileged and not parallel-safe (TESTING.md); T6 touches disjoint doc files and has no tests.

---

## Requirement Traceability

| Req | Task(s) | | Req | Task(s) |
| --- | --- | --- | --- | --- |
| FAIR-01 | T3 | | FAIR-15 | T2 |
| FAIR-02 | T3 | | FAIR-16 | T2 |
| FAIR-03 | T3 | | FAIR-17 | T2 |
| FAIR-04 | T3 | | FAIR-18 | T2, T6 |
| FAIR-05 | T3, T4 | | FAIR-19 | T2 |
| FAIR-06 | T3 | | FAIR-20 | T1 |
| FAIR-07 | T3 | | FAIR-21 | T2, T3, T5 |
| FAIR-08 | T1, T2, T3 | | FAIR-22 | T1 |
| FAIR-09 | T3 | | FAIR-23 | T2 (harness), T5 (loader) |
| FAIR-10 | T3 | | FAIR-24 | T4 (dp-unit), T5 (smoke) |
| FAIR-11 | T3 | | FAIR-25 | T3 |
| FAIR-12 | T5 | | FAIR-26 | T4, T5 |
| FAIR-13 | T3, T6 | | FAIR-27 | T6 |
| FAIR-14 | T2 | | | |

**Coverage:** 27 total, 27 mapped, 0 unmapped.
