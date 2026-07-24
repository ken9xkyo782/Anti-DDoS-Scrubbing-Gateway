# Per-CPU Committed Bucket (C1) Tasks

**Design**: [`design.md`](design.md) · **Spec**: [`spec.md`](spec.md) · **Context**: [`context.md`](context.md)
**Status**: Complete (All 10 tasks executed & verified)

---

## Baselines (pin **live** at Execute — do not trust the docs)

| Baseline | Expected | Note |
| --- | --- | --- |
| `B_dp` = `make -C data-plane test` | **137** | TESTING.md says 130, ROADMAP says 137 — measure it. Same trap B2's T11 hit. |
| `B_cp` = `pytest -q` | ≥610 passed / **6 pre-existing reds** | Only relevant to T10 (P2). See `[[cp-suite-preexisting-failures]]`. |
| `bench` clean_redirect | ~620 ns median | 2026-07-23, **with** `PKT_TEST_HOOKS` + spin-lock probe (R4). |

**Net dp-unit effect of this feature: 137 → 137.** T4 deletes
`test_fair_committed_spin_lock_mutates_tokens` (−1) and adds
`test_fair_committed_state_is_percpu` (+1). No silent deletions.

---

## Execution Plan

### Phase 0 — Measurement (must complete before any code change)

```
T1 ──→ T2 ⟵ R1 de-risk gate: proceed or escalate
```

### Phase 1 — The change (strictly sequential; shared C files)

```
T2 ──→ T3 ──→ T4 ──→ T5
```

### Phase 2 — Verification

```
T5 ──→ T6
```

### Phase 3 — Record (T7 ∥ T8 parallel; T9 needs T6's numbers)

```
        ┌──→ T7 [P]
T4 ─────┤
        └──→ T8 [P]
T6 ─────────→ T9
```

### Phase 4 — P2, independent and deferrable

```
T10  (no dependency on T1–T9; must not gate P1)
```

---

## Parallel Execution Map

```
Phase 0 (sequential, privileged):
  T1 ──→ T2

Phase 1 (sequential — all three tasks edit data-plane/src + tests/test_parse.c):
  T3 ──→ T4 ──→ T5

Phase 2 (sequential, privileged):
  T6

Phase 3:
  ├── T7 [P]  ┐ docs only, disjoint files, Tests: none
  ├── T8 [P]  ┘
  └── T9      (after T6 — needs measured numbers)

Phase 4 (optional):
  T10         (integration tests ⇒ never [P])
```

**Why so little parallelism:** T3/T4 both edit `tests/test_parse.c` and T4/T5 both depend on the
built object — shared mutable state. `dp-integration` is explicitly **not** parallel-safe
(TESTING.md), which serialises T2, T5 and T6. Only the two documentation tasks qualify for `[P]`.

---

## Task Breakdown

### T1: Build the multi-core contention harness

**What**: New `bench_mc.c` — N threads pinned to N distinct CPUs, all running `BPF_PROG_TEST_RUN`
against **one** `service_id` — plus its `make benchmc` target.
**Where**: `data-plane/tests/bench_mc.c` (new), `data-plane/Makefile` (modify),
`data-plane/tests/test_parse.c` (generalise `pin_to_cpu0()` → `pin_to_cpu(int)`)
**Depends on**: None — **lands against unmodified, still-locked code**
**Reuses**: `bench_dp.c` (`TEST_PARSE_NO_MAIN` include trick, `cmp_double`, result plumbing),
`bench_setup_clean_redirect()` / `bench_setup_bogon()`, `test_parse.c` env + seed harness
**Requirement**: CPB-01, CPB-02, CPB-03, CPB-04, CPB-05, CPB-06

**Tools**: MCP: NONE · Skill: `coding-guidelines`

**Done when**:

- [ ] Sweeps `N ∈ {1,2,4,8,16,…}` up to available cores, stopping at the real core count (CPB-01)
- [ ] Benches **both** `clean_redirect` (subject) and `bogon_drop` (lock-free control) each run
- [ ] Reports per-thread `opts.duration` median, wall-clock aggregate Mpps, scaling efficiency, and
      **relative efficiency** (subject ÷ control) (CPB-02, D-042-2/3)
- [ ] Prints min/median/max spread across rounds rather than hiding it (CPB-04)
- [ ] **CPU-spread self-check**: after each N-run, reads per-CPU `counter_map` across all possible
      CPUs and asserts **exactly N slots advanced**; a failed check **aborts** instead of printing a
      number (CPB-01, CPB-04)
- [ ] Control-curve check wired: if the lock-free control also flattens, the harness says so
- [ ] `--cpus a,b,c` override; default `0..N-1` with an SMT caveat in the banner (design §4.4)
- [ ] Non-root / no-JIT fails with `bench_dp`'s diagnostics, never a misleading number (CPB-05)
- [ ] Coordinator does all map seeding with **no worker threads alive** (design §4.1)
- [ ] No fairness reseeding added — `fair_default_config()` suffices (design F2, CPB-03)
- [ ] Gate check passes: `make -C data-plane bpf skel loader apply dpstat && make -C data-plane benchmc`
- [ ] **Self-verified**: `sudo build/bench_mc 50000 3 --max-threads 2` prints two rows with
      `cpus_advanced ok` on both

**Tests**: none — userspace measurement tooling; the DP-established pattern for `dpstat`
subcommands (STATE AD-036 DT1). **Not deferral**: the binary asserts its own preconditions in
Done-when above and is exercised end-to-end by T2.
**Gate**: build

**Verify**: `sudo make -C data-plane benchmc` → table renders, `rel_eff` column present, every row
ends `ok`.

**Commit**: `test(dp): add multi-core contention benchmark for the committed tier`

---

### T2: Capture the "before" curve and settle R1

**What**: Run T1's harness on unmodified code, record the locked-path scaling curve in `design.md`,
and **decide R1**: is the contention measurable at all?
**Where**: `.specs/features/committed-percpu-bucket/design.md` (§4.5 replaced with real numbers)
**Depends on**: T1
**Reuses**: —
**Requirement**: CPB-07

**Tools**: MCP: NONE · Skill: NONE

**Done when**:

- [ ] Full sweep run as root on an idle host; host, kernel, CPU model and `bpf_jit_enable` recorded
- [ ] Before-curve table pasted into design §4.5, replacing the illustrative sketch (CPB-07)
- [ ] Every row's `cpus_advanced` check passed
- [ ] **R1 verdict recorded, one of:**
      **(a) PROCEED** — control scales ≈ linearly while the subject flattens ⇒ contention is real and
      measurable; or
      **(b) ESCALATE** — control also flattens ⇒ the harness/host cannot measure this. **Stop and
      report to the user**; C1 may still land on correctness/simplification grounds, but the scaling
      claim must be marked *unverified* in every document and `CPB-16` reduced to "no regression".
- [ ] Two runs agree within the reported spread (CPB-04)

**Tests**: dp-integration (privileged, not parallel-safe)
**Gate**: build

**Verify**: two independent sweeps produce the same shape; `rel_eff` at max N is recorded.

**Commit**: `docs(dp): record pre-C1 committed-bucket contention curve`

---

### T3: Collapse the three per-CPU bucket readers onto one helper

**What**: Pure refactor — `read_fair_burst_bucket_cpu0()` and `read_fair_node_bucket_cpu0()` become
callers of a single `read_percpu_bucket_cpu0(env, fd, key, struct rl_bucket *out)`.
**Where**: `data-plane/tests/test_parse.c`
**Depends on**: T2 (nothing may change before the before-curve exists)
**Reuses**: the existing two bodies ([test_parse.c:1208](../../../data-plane/tests/test_parse.c#L1208))
**Requirement**: CPB-17 (prepares it)

**Tools**: MCP: NONE · Skill: `coding-guidelines`

**Done when**:

- [ ] One helper; both existing readers delegate to it; no behaviour change
- [ ] Gate check passes: `make -C data-plane test`
- [ ] Test count: **137** tests pass — unchanged (no silent deletions)

**Tests**: dp-unit
**Gate**: quick

**Verify**: `make -C data-plane test` → 137, diff touches only `test_parse.c`.

**Commit**: `refactor(dp): share one per-CPU bucket read helper across fairness tests`

**Why separate from T4**: keeps the risky conversion diff small and behaviour-only.

---

### T4: Convert the committed bucket to per-CPU and delete the spin-lock ⭐

**What**: `svc_committed_state` becomes `PERCPU_HASH` of `struct rl_bucket`;
`fair_committed_admit()` becomes the `fair_burst_admit()` twin; the lock, its struct, its assert and
the FAIR-22 probe are deleted; committed tests move to per-CPU reads.
**Where**: `data-plane/src/fairness.h`, `data-plane/src/xdp_gateway.bpf.c`,
`data-plane/tests/test_parse.c`
**Depends on**: T3
**Reuses**: `fair_burst_admit()` + `fair_bps_bucket_reset/refill/consume()` + `rl_burst()` /
`rl_refill_dim()` / `rl_bucket_consume_raw()`; the `svc_burst_state` map stanza; T3's read helper
**Requirement**: CPB-08..15, CPB-17, CPB-18, CPB-19, CPB-20, CPB-23

**Tools**: MCP: NONE · Skill: `coding-guidelines`

**Done when**:

- [ ] `svc_committed_state` = `BPF_MAP_TYPE_PERCPU_HASH`, key `__u32`, value `struct rl_bucket`,
      `max_entries` still `FAIR_CONFIG_MAX_ENTRIES` (CPB-08)
- [ ] `struct fair_committed_bucket` + its `_Static_assert` + the "top-level HASH is required"
      comment deleted (CPB-09)
- [ ] `fair_committed_admit()` rewritten per design C-1 — per-CPU share via
      `rl_burst(committed_bps, rl_cpu_count(), tnr)`, lazy version reset, first-packet seed decides
      in-call (CPB-10, CPB-11, CPB-12)
- [ ] `fair_admit_stage()` **untouched**: same `FAIR_COMMITTED`, same fall-through to burst → node
      headroom, same frozen drop reasons (CPB-13); `committed_bps == 0` still admits nothing (CPB-14)
- [ ] No added map ops or `bpf_ktime_get_ns()` calls; lock/unlock removed (CPB-15)
- [ ] `fair_test_spin_lock_mutate()`, `FAIR_TEST_TRIGGER_SPIN_LOCK`, `FAIR_TEST_LOCK_SERVICE_ID`,
      `test_fair_spin_lock_probe()` and its call site all deleted (CPB-09, CPB-20)
- [ ] `test_fair_committed_spin_lock_mutates_tokens` deleted **and** its registry entry removed (−1)
- [ ] **New** `test_fair_committed_state_is_percpu`: `bpf_map_get_info_by_fd` on
      `svc_committed_state` asserts `type == BPF_MAP_TYPE_PERCPU_HASH` and `value_size == 32` (+1,
      machine-verifies CPB-08)
- [ ] `test_fair_committed_exact_admit_count` and `test_fair_zero_committed_uses_burst_only` keep
      their arithmetic; only the reader call and `tokens` → `bps_tokens` change (CPB-18, design F3)
- [ ] `clear_u32_hash_map(svc_committed_state_fd)` left **as-is** and verified working (CPB-19, F4)
- [ ] M3 gate scenario (flood A / starve B) still passes bit-for-bit (CPB-23)
- [ ] `grep -rn "spin_lock" data-plane/src data-plane/tests` → **0 hits**
- [ ] Gate check passes: `make -C data-plane test`
- [ ] Test count: **137** tests pass (−1 probe, +1 percpu assertion — no silent deletions)

**Tests**: dp-unit
**Gate**: quick

**Verify**:
```bash
make -C data-plane bpf
bpftool btf dump file data-plane/build/xdp_gateway.bpf.o format raw | grep -ci spin_lock   # 0
grep -rn "spin_lock" data-plane/src data-plane/tests                                        # 0
make -C data-plane test                                                                     # 137
```

**Commit**: `perf(dp)!: move the committed bucket to per-CPU and drop the global spin-lock`

**Atomic by necessity** — deleting `struct fair_committed_bucket` breaks `test_parse.c`
compilation, so the header, the probe and the tests cannot land separately without a
non-compiling intermediate. Same rationale as B2's T5 wire bump.

---

### T5: Rescale the live fairness smoke for per-CPU committed

**What**: One-line rescale of `COMMITTED_BPS` by the host CPU count, then prove the smoke still
asserts what it exists to assert.
**Where**: `data-plane/tests/smoke_fairness.sh`
**Depends on**: T4
**Reuses**: the script's existing `POSSIBLE_CPUS` detection and `CEILING_BPS` derivation
**Requirement**: CPB-21, CPB-22

**Tools**: MCP: NONE · Skill: `coding-guidelines`

**Done when**:

- [ ] `COMMITTED_BPS=$((FRAME_LEN * 2 * POSSIBLE_CPUS))`; `CEILING_BPS` and
      `XDPGW_NODE_CLEAN_CAPACITY_BPS` formulas untouched (CPB-21, design F5)
- [ ] Live run: **exactly 2 frames redirected** (unchanged assertion)
- [ ] Live run: `service_ceiling_drop`, `congestion_drop`, `ingress_cap_drop` **all positive**;
      observed values recorded (design F5 predicts 2 / 10 / 4) (CPB-22)
- [ ] **R3**: if the ingress-cap margin proves fragile, lower `XDPGW_FAIR_K` in the smoke — do
      **not** revert the rescale; record whichever path was taken
- [ ] Gate check passes: `make -C data-plane test && sudo make -C data-plane smoke`
- [ ] Test count: 137 dp-unit + redirect/fairness/apply smokes all pass

**Tests**: dp-integration (privileged, not parallel-safe)
**Gate**: full

**Verify**: `sudo make -C data-plane smoke` → all three smokes pass; `dpstat counters` shows the
three fairness reasons non-zero.

**Commit**: `test(dp): scale the fairness smoke committed rate by CPU count`

---

### T6: Capture the "after" curve and the single-core delta

**What**: Re-run T1's harness and `make bench` on the converted code; compare against T2.
**Where**: `.specs/features/committed-percpu-bucket/design.md` (§8 results)
**Depends on**: T5
**Reuses**: T1 harness, existing `bench` target
**Requirement**: CPB-16, CPB-32 (measurement half)

**Tools**: MCP: NONE · Skill: NONE

**Done when**:

- [ ] Same host, kernel and build flags as T2 — stated explicitly (design §8)
- [ ] After-curve recorded; **relative efficiency at max N materially better than T2's** (CPB-16)
- [ ] `make -C data-plane bench` shows `clean_redirect` **≤ ~620 ns** — no single-core regression
      (CPB-15, CPB-32)
- [ ] R4 noted when quoting the delta: the test object also lost the probe's entry overhead
- [ ] If R1 was escalated in T2, this task records "no regression" only and says the scaling claim
      is unverified

**Tests**: dp-integration (privileged, not parallel-safe)
**Gate**: build

**Verify**: before/after tables side by side in design §8 with the host line above them.

**Commit**: `docs(dp): record post-C1 contention and single-core benchmark results`

---

### T7: Amend the committed guarantee where it is specified `[P]`

**What**: Rewrite every requirement/design statement that claims the committed bucket is exact.
**Where**: `.specs/features/fairness-bandwidth/spec.md` (banner + `FAIR-05` AC#2 + L45-47, L89,
L141-145), `.specs/features/fairness-bandwidth/design.md` (AD-025 note),
`.specs/project/TDD.md` (§4.3 map row, §4.4 mechanism 1, §13 risk row, glossary), `PRD.md` (§15 /
CM-04 framing), `.specs/project/ROADMAP.md` (M3 fairness entry + AD-025 line)
**Depends on**: T4
**Reuses**: the `⚠️ amended` banner + `*(amended)*` marker style from
[service-blacklist-removal/spec.md](../service-blacklist-removal/spec.md)
**Requirement**: CPB-24, CPB-25, CPB-26

**Tools**: MCP: NONE · Skill: `docs-writer`

**Done when**:

- [ ] All six rows of the spec's Superseded Requirements table edited in place (CPB-24)
- [ ] Each amendment states both halves: isolation unchanged and structural; full-rate delivery now
      RSS-dependent (CPB-25)
- [ ] The sub-MTU pathology is quantified concretely, naming fall-through-to-burst (not drop)
      (CPB-26)
- [ ] **R6 respected**: the *engineering* statement is amended; the customer-facing commercial
      wording in PRD §15 is flagged inline for the user, not silently rewritten
- [ ] No document still claims exactness or RSS-independence

**Tests**: none (documentation)
**Gate**: none

**Verify**: `grep -rn "spin_lock\|spin-lock" PRD.md .specs/ docs/` → only historical references.

**Commit**: `docs!: committed bandwidth is per-CPU and RSS-dependent (amends FAIR-05)`

---

### T8: Update the operational docs `[P]`

**What**: Fairness testing conventions and the data-plane map table.
**Where**: `.specs/codebase/TESTING.md` (fairness conventions, ~L299),
`data-plane/README.md` (map table)
**Depends on**: T4
**Reuses**: existing burst/node/cap convention wording
**Requirement**: CPB-27, CPB-28

**Tools**: MCP: NONE · Skill: `docs-writer`

**Done when**:

- [ ] TESTING.md instructs future committed tests to use the CPU-pinned runner + `test_no_refill`,
      exactly like burst/node/cap; the "does not depend on CPU pinning because it is global and
      spin-locked" sentence is gone (CPB-27)
- [ ] README map table shows `svc_committed_state` as per-CPU with `rl_bucket` values (CPB-28)
- [ ] The dp-unit count stated in TESTING.md is reconciled with the measured baseline (R2)

**Tests**: none (documentation)
**Gate**: none

**Verify**: grep TESTING.md and README for stale "global"/"spin-locked" committed claims → none.

**Commit**: `docs(dp): committed bucket is per-CPU in testing conventions and map table`

---

### T9: Re-baseline the performance record

**What**: Mark C1 done in the perf report with real numbers; refresh the benchmark memory.
**Where**: `docs/danh-gia-hieu-nang-data-plane.md` (§3 note, §6 finding, §8.4 C1, §8.6 table),
memory `dp-load-benchmark.md`
**Depends on**: T6
**Reuses**: the "✅ Đã hoàn thành" style B2 already uses in §8.3
**Requirement**: CPB-29, CPB-32, CPB-33

**Tools**: MCP: NONE · Skill: `docs-writer`

**Done when**:

- [ ] §8.4 C1 marked done with the measured before/after scaling numbers and the host line (CPB-29)
- [ ] §6's "spin-lock is the multi-core bottleneck" finding updated — it is no longer true (CPB-33)
- [ ] §3 clean-accept figure refreshed or annotated with the new measurement (CPB-32)
- [ ] §8.6 priority table: C1 row marked done; remaining items renumbered if needed
- [ ] `dp-load-benchmark` memory updated: new bottleneck ranking + `make benchmc` documented
      (CPB-33)

**Tests**: none (documentation)
**Gate**: none

**Verify**: perf report §8.4 shows measured numbers, not estimates; memory file mentions `benchmc`.

**Commit**: `docs(dp): mark C1 done and re-baseline the performance record`

---

### T10: P2 — low-committed-rate advisory (control plane)

**What**: Warn (never block) when a plan's committed rate divided by the node CPU count falls below
one MTU.
**Where**: `control-plane/app/services/` (the `ServicePlan` validation path that already emits the
SRL-36 oversubscription warning) + `control-plane/tests/integration/`
**Depends on**: None — **must not gate P1**
**Reuses**: the SRL-36 oversubscription warning surface and its tests
**Requirement**: CPB-30, CPB-31

**Tools**: MCP: NONE · Skill: `coding-guidelines`

**Done when**:

- [ ] **R5 resolved first**: pick the node-CPU-count source (settings value vs node-health payload
      vs `dpstat`) and record the choice — this is an open design question, not a given
- [ ] Warning (not a block) when `committed_bytes_per_sec / node_cpu_count < MTU` (CPB-30)
- [ ] The message names the CPU count and the computed per-core share (CPB-31)
- [ ] Plan creation still succeeds
- [ ] Gate check passes: `ruff check . && ruff format --check . && mypy app/ && pytest -q`
- [ ] Test count: `B_cp` + ≥2 new integration tests; the 6 pre-existing reds unchanged

**Tests**: integration (⇒ never `[P]`, per TESTING.md)
**Gate**: full

**Verify**: create a plan with a tiny committed rate against a high-core node profile → warning
present, creation succeeds.

**Commit**: `feat(cp): warn when a plan's per-core committed share is below one MTU`

---

## Pre-Approval Check 1 — Task Granularity

| Task | Scope | Status |
| --- | --- | --- |
| T1 | 1 new binary + 1 make rule + 1 helper generalisation | ✅ Granular (cohesive: the harness and its build) |
| T2 | 1 measurement run + 1 doc section | ✅ Granular |
| T3 | 1 function extraction, 1 file | ✅ Granular |
| T4 | 3 files, **1 concept** (the committed bucket) | ⚠️ **Cohesive-not-split** — deleting the struct breaks `test_parse.c` compilation, so no smaller unit is buildable. Precedent: B2 T5 (wire v4). |
| T5 | 1 line + 1 privileged run | ✅ Granular |
| T6 | 2 measurement runs + 1 doc section | ✅ Granular |
| T7 | 5 documents, 1 concern (the guarantee) | ✅ Granular (single coherent amendment) |
| T8 | 2 documents, 1 concern (conventions) | ✅ Granular |
| T9 | 1 report + 1 memory file | ✅ Granular |
| T10 | 1 validation rule + its tests | ✅ Granular |

---

## Pre-Approval Check 2 — Diagram ↔ Definition Cross-Check

| Task | `Depends on` (body) | Diagram arrows | Status |
| --- | --- | --- | --- |
| T1 | None | (root of Phase 0) | ✅ Match |
| T2 | T1 | `T1 → T2` | ✅ Match |
| T3 | T2 | `T2 → T3` | ✅ Match |
| T4 | T3 | `T3 → T4` | ✅ Match |
| T5 | T4 | `T4 → T5` | ✅ Match |
| T6 | T5 | `T5 → T6` | ✅ Match |
| T7 `[P]` | T4 | `T4 → T7` | ✅ Match |
| T8 `[P]` | T4 | `T4 → T8` | ✅ Match |
| T9 | T6 | `T6 → T9` | ✅ Match |
| T10 | None | (isolated Phase 4) | ✅ Match |

`[P]` pair T7/T8 do not depend on each other and touch disjoint files. ✅

---

## Pre-Approval Check 3 — Test Co-location Validation

| Task | Layer created/modified | Matrix requires | Task says | Status |
| --- | --- | --- | --- | --- |
| T1 | userspace measurement binary (`tests/bench_mc.c`) | *(no matrix row; DP tooling precedent = none + build)* | none | ✅ OK — self-verified in Done-when + exercised by T2; **not** deferral |
| T2 | privileged benchmark run | dp-integration | dp-integration | ✅ OK |
| T3 | dp-unit test harness (`test_parse.c`) | dp-unit | dp-unit | ✅ OK |
| T4 | XDP hot path + dp-unit tests | dp-unit | dp-unit | ✅ OK — tests land in the same task (−1/+1 stated) |
| T5 | privileged veth smoke | dp-integration | dp-integration | ✅ OK |
| T6 | privileged benchmark run | dp-integration | dp-integration | ✅ OK |
| T7 | documentation | none | none | ✅ OK |
| T8 | documentation | none | none | ✅ OK |
| T9 | documentation | none | none | ✅ OK |
| T10 | CP service layer + API validation | integration | integration | ✅ OK |

**All three checks pass.**

---

## Requirement Traceability

| Task | Requirements |
| --- | --- |
| T1 | CPB-01, 02, 03, 04, 05, 06 |
| T2 | CPB-07 |
| T3 | CPB-17 (prep) |
| T4 | CPB-08, 09, 10, 11, 12, 13, 14, 15, 17, 18, 19, 20, 23 |
| T5 | CPB-21, 22 |
| T6 | CPB-16, 32 (measurement) |
| T7 | CPB-24, 25, 26 |
| T8 | CPB-27, 28 |
| T9 | CPB-29, 32, 33 |
| T10 | CPB-30, 31 |

**Coverage: 33 total, 33 mapped, 0 unmapped.** ✅

---

## Tools

Per STATE `Preferences`: **Skill `coding-guidelines`** on every C/XDP code task (T1, T3, T4, T5) and
on T10; **Skill `docs-writer`** on T7, T8, T9. **No MCPs configured** for this project.

---

## Risks Carried into Execute

| # | Risk | Handling |
| --- | --- | --- |
| **R1** | Concurrent `BPF_PROG_TEST_RUN` may not parallelise ⇒ contention unmeasurable | **T2 is the gate.** Control curve decides PROCEED vs ESCALATE. Escalation stops for user input; it does not silently downgrade the claim. |
| R2 | dp-unit baseline disputed in docs (130 vs 137) | Pin live in T3; T8 reconciles the docs. |
| R3 | Smoke ingress-cap margin narrows to ~4 drops | T5 verifies **live**; if fragile, lower `XDPGW_FAIR_K`, never revert the rescale. |
| R4 | Probe removal also lightens the test object | T6 states it when quoting the delta; same-object before/after is unaffected. |
| R5 | P2 needs a node CPU count the CP lacks | First checkbox of T10; P2 cannot gate P1. |
| R6 | PRD §15 / CM-04 wording is customer-facing | T7 amends engineering text and **flags** the commercial wording for the user. |
