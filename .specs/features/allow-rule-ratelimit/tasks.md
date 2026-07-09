# Allow-Rule Matching & Rate-Limit Tasks

**Design**: `.specs/features/allow-rule-ratelimit/design.md` (Approved 2026-07-09)
**Status**: Approved (2026-07-09) → Execute
**Baseline**: `cd data-plane && make test` → **34 passed**; `make bpf skel loader dpstat` green.
**Gates** (from `.specs/codebase/TESTING.md`, run in `data-plane/`): build = `make bpf skel loader dpstat` ·
quick = `make test` · full = `make test && sudo make smoke`.
**Tools** (STATE preference, applies to every task): Skill `coding-guidelines` on C/XDP code; MCP: NONE.

---

## Execution Plan

```
Phase 1 (sequential):   T1 ──→ T2 ──→ T3
Phase 2 (fan-out):                    T3 ──┬──→ T4        (full gate; smoke not parallel-safe)
                                           └──→ T5 [P]    (docs only; parallel with T4)
```

Only T5 is `[P]`: T1–T3 serialize on shared files (`rules.h`, `xdp_gateway.bpf.c`, `test_parse.c`);
T4 runs the privileged smoke (dp-integration, Parallel-Safe: No per TESTING.md).

---

## Task Breakdown

### T1: Rule contracts, maps & verifier de-risk

**What**: `src/rules.h` with the frozen contract structs (`rule_entry`, `rule_block`, `rl_key`,
`rl_bucket`, `rl_config`, flags, `RULE_MAX`, `RULE_PROTO_ANY`) + all four map definitions
(`rule_block_0/1`, `rule_block_map` ARRAY_OF_MAPS, `rate_limit_state` PERCPU_HASH prealloc 16384,
`rl_config`) + `const volatile __u32 rl_ncpus` rodata + `pkt_meta.rule_idx` (takes one pad byte) —
and the **fail-fast de-risk**: a compiled-in bounded-16 loop over a `rule_block` behind a stage
function that is **not yet wired** (verdicts unchanged), proving the verifier accepts map-in-map
HASH inner + 520-byte value + the loop shape before anything depends on it (design Research #3/#4).
**Where**: `data-plane/src/rules.h` (NEW), `data-plane/src/pkt_meta.h`,
`data-plane/src/xdp_gateway.bpf.c` (include + unwired stage fn)
**Depends on**: None
**Reuses**: `service_map` double-buffer shape (`xdp_gateway.bpf.c`), `sample.h` map-definition style
**Requirement**: ARL-18, ARL-23 (load de-risk half), A-ARL-8

**Done when**:

- [x] `rules.h` matches the design Data Models section field-for-field (incl. the bytes/sec `bps`
      unit note + pre-sorted-position contract comment for M4)
- [x] `pkt_meta` size unchanged (`_Static_assert(sizeof == 32)` or equivalent guard)
- [x] Program with maps + unwired loop **loads** under the test harness (verifier pass = de-risk
      proven); if the verifier rejects map-in-map HASH inner, STOP and record the fallback
      (two named top-level HASH maps + slot branch, AD-015 precedent) before proceeding
- [x] Gate check passes: `make bpf skel loader dpstat` && `make test`
- [x] Test count: 34 pass (behavior unchanged — no silent deletions)

**Tests**: none new (dp-unit suite as regression harness)
**Gate**: build + quick
**Commit**: `feat(allow-rule): add rule/bucket map contracts and verifier de-risk`

---

### T2: Match engine — first-match stage, wire-in & suite migration

**What**: The full match half of `allow_rule_stage()` wired into `service_lookup_redirect()`'s
enabled hit: block lookup on the pinned slot (fail-closed `DR_MAP_ERROR` on slot-inner failure;
absent block / `rule_count==0` → `DR_NOT_ALLOWED`), `rule_count` clamp to 16, enabled-skip,
strict-proto match (`any` = {6,17,1}, D-ARL-1), inclusive port ranges with 0..65535 wildcard,
first-match terminal, `meta->rule_idx`, `admit_clean()` seam (marked M3#4 comment) → `redirect_out`.
Quota'd rules **admit unconditionally in this task** (buckets = T3). Includes the **ARL-22
migration**: `seed_rule_block()` test helper + match-all blocks for existing enabled-service cases,
plus new match-semantics cases (design Test Plan #2, #4–#7, #11, #12).
**Where**: `data-plane/src/rules.h`, `data-plane/src/xdp_gateway.bpf.c`,
`data-plane/tests/test_parse.c`
**Depends on**: T1
**Reuses**: `record_drop()` (index 9), `redirect_out()`/`test_meta_map`, `pkt_build.h` frame builders
**Requirement**: ARL-01..08, ARL-19, ARL-20 (index 9), ARL-22, ARL-24; D-ARL-1

**Done when**:

- [x] All 34 baseline cases pass migrated (enabled-service cases seed a match-all block)
- [x] New cases: first-match order + `rule_idx` observed; zero-rule and absent-block default-deny
      (counter 9 exact); disabled-skip; GRE vs `any` block → `not_allowed`; port boundaries
      79/80/81 on range 80–80 + src-only-range + wildcard; `rule_count=99` clamps to 16
- [x] ARP cases still redirect (stage untouched by non-service path)
- [x] Gate check passes: `make test`
- [x] Test count: 42 pass (34 migrated + 8 new; record exact N in this file on completion)

**Tests**: dp-unit
**Gate**: quick
**Commit**: `feat(allow-rule): first-match rule stage with default-deny and suite migration`

---

### T3: Rate-limit buckets — per-CPU quotas, lazy version reset, deterministic mode

**What**: The quota half: `rl_bucket_admit()` in `rules.h` — per-CPU `rate_limit_state` bucket with
`cfg_version` lazy reset (D-ARL-2), remainder-preserving ns refill at rate÷`rl_ncpus`, 1s-capped
elapsed, burst = `max(rate_percpu, 1)`, both-dims-or-neither consumption (ARL-12), `PPS_SET`/`BPS_SET`
flag semantics (NULL = unlimited, 0 = block), insert-on-miss with fail-closed `DR_MAP_ERROR`,
`rl_config.test_no_refill` deterministic mode (quota value = total per-CPU budget). Test runner
gains `sched_setaffinity` CPU-0 pinning + sets `rl_ncpus` rodata pre-load; quota cases per design
Test Plan #3, #8–#10.
**Where**: `data-plane/src/rules.h`, `data-plane/tests/test_parse.c`
**Depends on**: T2
**Reuses**: `sample.h` bucket refill structure, `record_drop()` (index 10), T2's `seed_rule_block()`
**Requirement**: ARL-09..17, ARL-20 (index 10); D-ARL-2

**Done when**:

- [ ] `test_no_refill=1`, `pps=3` → exactly 3 admits then `rate_limit_drop` (counter 10 exact)
- [ ] No fall-through: overflow of a quota'd rule never reaches a later match-all rule
- [ ] No-quota rule always admits; `pps=0`+flag always drops; `bps` exhausts by bytes; a
      pps-exhausted drop leaves `bps_tokens` untouched
- [ ] Reset-on-swap: exhaust quota, rewrite block `version+1` → next packet admits
- [ ] Normal mode (`test_no_refill=0`): first packet on a fresh bucket admits (init path)
- [ ] Gate check passes: `make test`
- [ ] Test count: ≥ 49 pass (T2's N + ≥ 7 new; record exact N on completion)

**Tests**: dp-unit
**Gate**: quick
**Commit**: `feat(allow-rule): per-rule aggregate token buckets with lazy version reset`

---

### T4: Loader seed extension & live smoke

**What**: Loader seeds, for each seeded service, a match-all `rule_block` `{version=1, rule_count=1,
[any/wildcard/no-quota/enabled]}` into **both** `rule_block_0/1` inners, and sets `rl_ncpus` rodata
(`libbpf_num_possible_cpus()`) before load — keeping the gated two-veth smoke's forwarding +
TTL/checksum assertions green with the rule stage live (ARL-23's runtime half, D-SLRD-1 posture).
**Where**: `data-plane/loader/loader.c`
**Depends on**: T3
**Reuses**: existing seed helper + `smoke_redirect.sh` (unchanged)
**Requirement**: ARL-21, ARL-23

**Done when**:

- [ ] `make bpf skel loader dpstat` green; loader attaches native/DRV fail-loud
- [ ] Gate check passes: `make test && sudo make smoke` (dp-integration: frame forwarded `IN→OUT`,
      TTL + IPv4 checksum byte-identical, with the match-all block seeded)
- [ ] Test count: T3's N pass (unchanged)

**Tests**: dp-integration (existing smoke re-validated; not parallel-safe)
**Gate**: full
**Commit**: `feat(allow-rule): seed match-all rule blocks in loader for live smoke`

---

### T5: Documentation — TESTING.md conventions & README tunnel note [P]

**What**: TESTING.md data-plane section gains the M3 rule conventions (rule-block seeding via
`seed_rule_block()`, deterministic buckets via `rl_config.test_no_refill` + CPU-pinned runner,
updated baseline count); `data-plane/README.md` gains the D-ARL-1 note (non-TCP/UDP/ICMP IPv4 —
GRE/ESP/etc. — always drops `not_allowed`; sustained `not_allowed` from tunnel traffic is expected)
and the `rules.h`-is-the-M4-contract pointer (pre-sorted order, bytes/sec `bps`).
**Where**: `.specs/codebase/TESTING.md`, `data-plane/README.md`
**Depends on**: T3 (documents its conventions; no code dependency on T4)
**Reuses**: existing TESTING.md data-plane section structure (A-PKT-2 / A-SLRD-8 precedent)
**Requirement**: ARL-25; D-ARL-1 (documentation half)

**Done when**:

- [ ] Both docs updated; TESTING.md baseline count matches T3's recorded N
- [ ] Gate check passes: `make test` (docs-only change; count unchanged)
- [ ] Test count: T3's N pass

**Tests**: none (docs)
**Gate**: quick (regression only)
**Commit**: `docs(allow-rule): rule-stage test conventions and tunnel-traffic note`

---

## Pre-Approval Checks

### Check 1 — Granularity

| Task | Scope | Status |
| --- | --- | --- |
| T1 | 1 new header + 2 surgical edits, no behavior change | ✅ Granular (contract + de-risk is one concept, AD-015/SLRD-T2 precedent) |
| T2 | 1 stage function + wire-in + its tests (co-located migration) | ✅ Cohesive (migration cannot land separately — suite must pass at task end) |
| T3 | 1 bucket function + its tests | ✅ Granular |
| T4 | 1 file (loader), 1 seed extension | ✅ Granular |
| T5 | 2 docs | ✅ Granular |

### Check 2 — Diagram-Definition Cross-Check

| Task | Depends On (body) | Diagram shows | Status |
| --- | --- | --- | --- |
| T1 | None | start of chain | ✅ Match |
| T2 | T1 | T1 → T2 | ✅ Match |
| T3 | T2 | T2 → T3 | ✅ Match |
| T4 | T3 | T3 → T4 | ✅ Match |
| T5 | T3 | T3 → T5 [P] | ✅ Match (parallel with T4, no shared files) |

### Check 3 — Test Co-location (vs TESTING.md matrix)

| Task | Layer created/modified | Matrix requires | Task says | Status |
| --- | --- | --- | --- | --- |
| T1 | contracts/maps, no verdict change | dp-unit as regression | none new + quick gate | ✅ OK (de-risk load is the verification) |
| T2 | parser/verdict path | dp-unit | dp-unit | ✅ OK |
| T3 | verdict path (buckets) | dp-unit | dp-unit | ✅ OK |
| T4 | loader + live redirect path | dp-integration | dp-integration | ✅ OK |
| T5 | docs | none | none | ✅ OK |

---

## Requirement Coverage

| Requirement | Task | | Requirement | Task |
| --- | --- | --- | --- | --- |
| ARL-01..08 | T2 | | ARL-18 | T1 |
| ARL-09..17 | T3 | | ARL-19 | T2 (block path) + T3 (bucket path) |
| ARL-20 | T2 (idx 9) + T3 (idx 10) | | ARL-21 | T4 |
| ARL-22 | T2 | | ARL-23 | T1 (verifier) + T4 (live) |
| ARL-24 | T2 | | ARL-25 | T5 |

**Coverage: 25/25 requirements mapped; 0 unmapped.**
