# Per-CPU Committed Bucket (C1 — Drop the Global Spin-Lock) Specification

> Source: [danh-gia-hieu-nang-data-plane.md §8.4 C1](../../../docs/danh-gia-hieu-nang-data-plane.md#84-nhóm-3--cấu-trúc--mở-rộng-đa-lõi)
> — *"Chuyển committed bucket sang per-CPU (bỏ spin-lock global). Các bucket khác (cap/burst/VIP/svc_rl)
> **đã** là PERCPU; chỉ committed còn dùng spin-lock để chính xác tuyệt đối. Đổi sang per-CPU (mỗi lõi
> nhận `committed_bps/ncpus`) sẽ **xoá nút cổ chai đa lõi** — đánh đổi là sai số token nhỏ ở tốc độ thấp."*

> **⚠️ This feature amends a shipped requirement.** `FAIR-05` and TDD §4.4 currently specify the
> committed bucket as **exact, independent of RSS/CPU distribution**. C1 deliberately trades that
> exactness for multi-core scalability. See [Superseded Requirements](#superseded-requirements).

## Problem Statement

`fair_committed_admit()` ([fairness.h:368](../../../data-plane/src/fairness.h#L368)) takes a
`bpf_spin_lock` on **one global bucket per service** ([fairness.h:400](../../../data-plane/src/fairness.h#L400))
for every packet that reaches the admit ladder. Every other token bucket in the data plane —
ingress-cap, service burst, node headroom, VIP ceiling, per-rule rate-limit — is already
`PERCPU_HASH`/`PERCPU_ARRAY` and lock-free. Committed is the last hold-out.

When RSS spreads the traffic of **one hot VIP** across many cores, all of those cores contend on a
single cache line holding that service's lock. The 2026-07-23 load evaluation
([§6](../../../docs/danh-gia-hieu-nang-data-plane.md#6-phát-hiện-đáng-lưu-ý-cho-thiết-kế)) names this as
*the* scaling bottleneck: the drop paths scale near-linearly to 96 cores (~200–280 Mpps aggregate),
but the **accept** path for a single service does not. On a 96-core node that is the difference
between a per-core figure (~1.6 Mpps) and an aggregate one.

The lock is not the only cost it imposes: it forces `svc_committed_state` to be a **top-level** map
(`bpf_spin_lock` cannot live in an inner map — [fairness.h:88](../../../data-plane/src/fairness.h#L88)),
forces a bespoke 24-byte bucket struct with its own refill arithmetic
([fairness.h:401-427](../../../data-plane/src/fairness.h#L401)) that duplicates `rl_refill_dim()`
([rules.h:164](../../../data-plane/src/rules.h#L164)), and carries a test-only spin-lock probe
([fairness.h:118](../../../data-plane/src/fairness.h#L118), [xdp_gateway.bpf.c:120](../../../data-plane/src/xdp_gateway.bpf.c#L120))
kept from the FAIR-22 de-risk.

This feature converts committed to a per-CPU bucket on the established `rl_bucket` pattern, deletes
the lock and everything that existed to support it, and — because today's benchmark is single-CPU and
therefore blind to lock contention — adds the multi-core measurement that can actually prove the win.

## Goals

- [ ] The XDP object contains **zero** `bpf_spin_lock` instances; the clean-accept path performs no
      locked operation.
- [ ] The committed bucket is `PERCPU_HASH` of `struct rl_bucket`, refilled at `committed_bps/ncpus`
      per core through the existing `rl_burst()` / `rl_refill_dim()` helpers — one bucket
      implementation in the data plane, not two.
- [ ] A repeatable multi-core benchmark reports aggregate Mpps for the clean-accept path against a
      **single** `service_id` at 1, 2, 4, 8, 16 … cores, measured **before and after** the change.
- [ ] `make -C data-plane test` and the privileged fairness/redirect/apply smokes stay green, with
      committed-tier assertions rewritten for per-CPU semantics.
- [ ] The committed guarantee is restated honestly everywhere it is sold (PRD/TDD/spec/README/
      TESTING/alerting dimension) — no document is left claiming exactness that the code no longer
      provides.

## Non-Goals / Out of Scope

| Item | Reason |
| --- | --- |
| Burst, node-headroom, ingress-cap, VIP-ceiling and per-rule buckets | Already per-CPU. Untouched. |
| The admit ladder's **order** (committed → burst → node headroom) | Unchanged; only the committed tier's storage/accounting changes. |
| Drop-reason ABI (`service_ceiling_drop` 12, `congestion_drop` 13, `ingress_cap_drop` 11) | Frozen indices (AD-016/AD-017). No reason is added, removed or renumbered. |
| Control-plane `ServicePlan` schema, `committed ≤ ceiling` validation, oversubscription warning | Data-plane-only change. The wire contract and `fair_config` layout are byte-identical. |
| Billing / chargeback formula `billed = max(committed, p95)` | Unchanged. Metering reads `svc_stat` clean bytes, not the committed bucket. |
| Merging the committed and burst maps into one | Tempting (both become `PERCPU_HASH` of `rl_bucket`), but they hold independent token state per service. Separate maps stay. |
| Other perf items A1–A4, B1, B3, B4, C2, D1 | Separately specified. C1 must be measurable in isolation. |
| Recovering aggregate exactness via a global spill bucket | Explicitly rejected — see `D-CPB-1`. |

---

## Decisions Taken (pre-spec)

Settled via discussion before this spec; not open questions.

| # | Decision | Consequence |
| --- | --- | --- |
| D-CPB-1 | **Pure per-CPU split.** Each core is granted `committed_bps/ncpus`. No global spill bucket, no hybrid. | Committed becomes **statistically** honored, not exactly. Under RSS skew a service can fall through to the burst tier before its *aggregate* committed rate is reached. Requires the FAIR-05 / CM-04 amendment below. |
| D-CPB-2 | **Add a multi-CPU contention benchmark.** N threads pinned to N cores, all `BPF_PROG_TEST_RUN` on the **same** `service_id`. | The single-CPU `bench_dp` cannot see an uncontended lock (~20–40 ns); without this harness the headline claim stays unverified. New work on top of [bench_dp.c](../../../data-plane/tests/bench_dp.c). |
| D-CPB-3 | **Remove the spin-lock test probe** (`fair_test_spin_lock_mutate`, `FAIR_TEST_TRIGGER_SPIN_LOCK`, `FAIR_TEST_LOCK_SERVICE_ID`, `test_fair_spin_lock_probe`, `test_fair_committed_spin_lock_mutates_tokens`). | Dead code once nothing locks. The FAIR-22 kernel de-risk is retired, not preserved. dp-unit baseline drops by 1 test and must be re-pinned. |
| D-CPB-4 | **Reuse `struct rl_bucket`** (32 B) rather than a new lockless struct. | `struct fair_committed_bucket` and its `_Static_assert` are deleted; `fair_bps_bucket_reset/refill/consume` are reused verbatim, exactly like `fair_burst_admit()`. Test read helpers move to the per-CPU pattern. |

---

## Superseded Requirements

C1 cannot be implemented without contradicting text that is already shipped and, in one case, sold.
Each entry below is a required edit, not an incidental doc chore.

| Where | Current claim | After C1 |
| --- | --- | --- |
| [fairness-bandwidth/spec.md](../fairness-bandwidth/spec.md) `FAIR-05` (AC #2) | committed is "accounted **exactly** via a global (non-per-CPU) bucket protected by `bpf_spin_lock` so accuracy is independent of RSS/CPU distribution" | committed is accounted **per-CPU** at `committed_bps/ncpus`; accuracy depends on RSS distribution, with the same documented deviation the burst tier already carries. |
| [TDD.md §4.3 table](../../project/TDD.md) (`service_agg_rate_state` row), §4.4 mechanism 1, glossary "Token bucket" | "committed = global array + `bpf_spin_lock`", "chính xác bất kể phân bố RSS/CPU" | per-CPU for every tier; the accuracy caveat that today applies only to burst now applies to committed too. |
| [TDD.md §13 risk table](../../project/TDD.md) | risk "per-CPU token bucket sai số" is mitigated by "committed bucket dùng global+spin_lock để chính xác" | mitigation is replaced: the deviation is bounded and documented, and multi-core scalability is the reason. |
| PRD §15 / CM-04 "hard guarantee" framing | committed clean bandwidth is a **hard** guarantee under a neighbour flood | remains a per-service isolation guarantee (A's flood still cannot touch B's budget — that property is structural and **unchanged**), but delivery of the full committed rate to a *single* service is now subject to RSS distribution. |
| [TESTING.md](../../codebase/TESTING.md) fairness conventions (~L299) | "Committed-bucket assertions do not depend on CPU pinning because `svc_committed_state` is global and spin-locked" | committed assertions use the CPU-pinned runner and `test_no_refill`, identical to burst/node/cap. |
| [ROADMAP.md](../../project/ROADMAP.md) M3 fairness entry + AD-025 | "2-tier committed (global + `bpf_spin_lock`, exact)"; per-CPU split listed as a *fallback* | the documented fallback is now the shipped design; the entry records C1 and the measured scaling result. |

**Unchanged by design:** the M3 milestone gate itself — *flooding service A never starves service B's
committed bandwidth* — still holds and must still pass. Per-service buckets keep A's flood inside A's
own budget; that property never depended on the lock.

---

## User Stories

### P1: Contended multi-core benchmark (the "before" number) ⭐ MVP

**User Story**: As an engineer, I want a benchmark that saturates one `service_id` from many cores at
once, so that the spin-lock ceiling C1 exists to remove is a measured number rather than a claim.

**Why P1**: This story ships **first**, against the current locked code. The existing
[bench_dp](../../../data-plane/tests/bench_dp.c) pins to CPU 0, where an uncontended lock costs
almost nothing — it structurally cannot observe the bottleneck. Without a before/after pair, C1's
only verifiable outcome is "tests still pass".

**Acceptance Criteria**:

1. WHEN the contention benchmark is run THEN it SHALL spawn `N` threads, each pinned to a distinct
   CPU, each issuing `BPF_PROG_TEST_RUN` on the `clean_redirect` scenario against **the same**
   `service_id`, and SHALL report **aggregate Mpps** and per-thread ns/packet for
   `N ∈ {1, 2, 4, 8, 16, …}` up to the available core count. `(CPB-01)`
2. WHEN the benchmark reports a row THEN it SHALL also report **scaling efficiency**
   (`aggregate_Mpps(N) / (N × Mpps(1))`) so that sub-linear scaling is visible without arithmetic by
   the reader. `(CPB-02)`
3. WHEN the benchmark runs THEN the fairness budget for the benched service SHALL be seeded large
   enough that no packet is demoted to the burst tier, so the measurement is of the committed path
   and not of a tier transition. `(CPB-03)`
4. WHEN the benchmark is run twice on an idle host THEN the aggregate Mpps figures SHALL be stable
   within the ±1 % band the existing bench achieves, or the run SHALL report the observed spread so
   an unstable measurement is not mistaken for a result. `(CPB-04)`
5. WHEN the benchmark is invoked without root, or on a kernel without BPF JIT THEN it SHALL fail with
   the same diagnostic behaviour as `bench_dp` rather than reporting misleading numbers. `(CPB-05)`
6. WHEN the harness is added THEN it SHALL reuse the `test_parse.c` seed/env harness under
   `TEST_PARSE_NO_MAIN` in the manner `bench_dp.c` already does, and SHALL be reachable from a
   documented `make -C data-plane` target. `(CPB-06)`
7. WHEN the "before" run is complete THEN its numbers SHALL be recorded in the spec/design record so
   the post-change comparison has a fixed reference. `(CPB-07)`

**Independent Test**: On the current `main` (lock still present), run the new target and observe
aggregate Mpps flattening as `N` grows — scaling efficiency well below 1.0 at high `N`.

---

### P2: Lock-free per-CPU committed bucket ⭐ MVP

**User Story**: As a gateway operator, I want the committed tier to keep per-core token state, so
that clean traffic to one busy VIP scales with cores instead of serialising on one cache line.

**Why P1**: This is C1. Everything else in this spec supports or verifies it.

**Acceptance Criteria**:

1. WHEN the XDP object is built THEN `svc_committed_state` SHALL be
   `BPF_MAP_TYPE_PERCPU_HASH` keyed by `__u32 service_id` with value `struct rl_bucket`, with
   `max_entries` unchanged at `FAIR_CONFIG_MAX_ENTRIES`. `(CPB-08)`
2. WHEN the XDP object is built THEN it SHALL contain **no** `bpf_spin_lock` field, no
   `bpf_spin_lock()`/`bpf_spin_unlock()` call, and no `struct fair_committed_bucket`; the
   `_Static_assert` on that struct ([fairness.h:50](../../../data-plane/src/fairness.h#L50)) and the
   "top-level HASH is required" comment ([fairness.h:88](../../../data-plane/src/fairness.h#L88))
   SHALL be removed with it. `(CPB-09)`
3. WHEN a packet reaches the committed tier THEN admission SHALL be decided from the **current CPU's**
   bucket, refilled at `committed_bps` through `rl_refill_dim()`'s per-CPU denominator and capped at
   `rl_burst(committed_bps, rl_cpu_count(), test_no_refill)` — i.e. each core's share is
   `committed_bps/ncpus`, mirroring [`fair_burst_admit()`](../../../data-plane/src/fairness.h#L307)
   exactly. `(CPB-10)`
4. WHEN a service's `fair_config.version` changes (config swap) THEN the current CPU's committed
   bucket SHALL lazily reset to the new version's full per-CPU burst on its next packet, with no
   worker-side map plumbing — the same lazy-version-reset contract the other buckets use. `(CPB-11)`
5. WHEN the committed bucket for a `service_id` does not yet exist THEN the first packet SHALL seed
   it and decide admission from the seeded value in the same call, without a spurious drop and
   without `DR_MAP_ERROR`. `(CPB-12)`
6. WHEN the committed bucket has insufficient tokens THEN the packet SHALL fall through to the burst
   tier and then node headroom **exactly as today**, producing the same `fair_state` transitions and
   the same `service_ceiling_drop` / `congestion_drop` reasons at the same frozen indices. `(CPB-13)`
7. WHEN `committed_bps == 0` (best-effort-only plan, SRL-43) THEN the committed tier SHALL admit
   nothing and all clean traffic SHALL be served by the burst tier — behaviour identical to today.
   `(CPB-14)`
8. WHEN the whole feature is built THEN the accept path SHALL make **no more** map operations or
   `bpf_ktime_get_ns()` calls per packet than it does today. `(CPB-15)`
9. WHEN the contention benchmark from P1 is re-run after this change THEN aggregate Mpps SHALL rise
   with core count with materially better scaling efficiency than the recorded "before" run, and the
   single-core ns/packet SHALL not regress. `(CPB-16)`

**Independent Test**: `make -C data-plane bpf` then `bpftool prog dump xlated` / `llvm-objdump` the
object and grep for spin-lock helpers → zero hits; `bpftool map show` reports `svc_committed_state`
as `percpu_hash`; the contention bench shows the improved curve.

---

### P3: Deterministic tests and live smoke reflect per-CPU committed ⭐ MVP

**User Story**: As a maintainer, I want the fairness test suite to assert per-CPU committed
semantics, so that the gate keeps catching real regressions instead of encoding a design that no
longer exists.

**Why P1**: The dp-unit suite is the merge gate. Three tests read the committed bucket through a
struct that is being deleted, and the live fairness smoke seeds a committed rate that per-CPU
division makes unusable — the change cannot land without them.

**Acceptance Criteria**:

1. WHEN the dp-unit suite reads the committed bucket THEN it SHALL use a per-CPU read helper
   returning CPU 0's `struct rl_bucket` (the `read_fair_burst_bucket_cpu0()`
   [pattern](../../../data-plane/tests/test_parse.c#L1208)), and `read_fair_committed_bucket()`'s
   flat-value form SHALL be gone. `(CPB-17)`
2. WHEN committed-tier quotas are asserted THEN the test SHALL use the **CPU-pinned runner** with
   `rl_config.test_no_refill = 1`, under which `rl_burst()` returns the undivided rate — keeping
   `test_fair_committed_exact_admit_count` and `test_fair_zero_committed_uses_burst_only` exact.
   `(CPB-18)`
3. WHEN the env resets committed state between cases THEN it SHALL clear the map correctly as a
   per-CPU hash (today's `clear_u32_hash_map(env->svc_committed_state_fd)`
   [call](../../../data-plane/tests/test_parse.c#L493)). `(CPB-19)`
4. WHEN `test_fair_committed_spin_lock_mutates_tokens` and the `FAIR_TEST_TRIGGER_SPIN_LOCK` probe
   are removed per `D-CPB-3` THEN no other test, trigger value, or `expect_fd` assertion SHALL be
   left dangling, and the dp-unit baseline SHALL be re-pinned to the new count. `(CPB-20)`
5. WHEN [smoke_fairness.sh](../../../data-plane/tests/smoke_fairness.sh) seeds `COMMITTED_BPS` THEN
   the value SHALL be scaled by the host's possible-CPU count so that one core's share still covers
   the frames the smoke expects the committed tier to admit — today's `FRAME_LEN * 2`
   ([L27](../../../data-plane/tests/smoke_fairness.sh#L27)) divided across 96 CPUs floors to 1 byte
   and would admit **zero** 60-byte frames. `(CPB-21)`
6. WHEN the live fairness smoke runs after rescaling THEN it SHALL still observe its committed-tier
   admissions followed by positive `service_ceiling_drop`, `congestion_drop` and `ingress_cap_drop`
   counters — the assertions the smoke exists for. `(CPB-22)`
7. WHEN the M3 fairness gate scenario runs (flood A, then B's committed traffic, versus the no-flood
   control) THEN B's admission count SHALL still match the control bit-for-bit. `(CPB-23)`

**Independent Test**: `make -C data-plane test` green at the re-pinned baseline; `smoke_fairness.sh`
passes as root on veth.

---

### P4: The committed guarantee is restated where it is sold ⭐ MVP

**User Story**: As a product owner, I want every document that promises exact committed bandwidth to
say what the code actually does, so that SLA, alerting and billing statements stay defensible.

**Why P1**: `committed_clean_gbps` is a contractual, billed quantity (`billed = max(committed, p95)`)
and an alerting dimension ("committed honored"). Shipping a silent accuracy downgrade behind
unchanged marketing text is the one outcome this feature must not produce.

**Acceptance Criteria**:

1. WHEN this feature lands THEN every row of the [Superseded Requirements](#superseded-requirements)
   table SHALL be edited in place, each amendment marked as amended-by-C1 in the house style used by
   [service-blacklist-removal/spec.md](../service-blacklist-removal/spec.md). `(CPB-24)`
2. WHEN the amended text describes the guarantee THEN it SHALL state both halves plainly: per-service
   **isolation** under a neighbour flood is unchanged and structural; full-rate **delivery** to a
   single service is now subject to RSS distribution across cores. `(CPB-25)`
3. WHEN the deviation is documented THEN it SHALL quantify the pathology concretely — a service whose
   `committed_bps / ncpus` is below one MTU cannot admit a full-size frame from the committed tier on
   any core — and name the resulting behaviour (fall-through to burst, not a drop). `(CPB-26)`
4. WHEN [TESTING.md](../../codebase/TESTING.md) is updated THEN the fairness conventions SHALL
   instruct future tests to treat committed exactly like burst/node/cap: CPU-pinned runner plus
   `test_no_refill`. `(CPB-27)`
5. WHEN [data-plane/README.md](../../../data-plane/README.md) is updated THEN its map table SHALL
   show `svc_committed_state` as per-CPU with the `rl_bucket` value type. `(CPB-28)`
6. WHEN the perf report §8.4/§8.6 is updated THEN C1 SHALL be marked done with its measured
   before/after scaling numbers, in the manner B2 is recorded today. `(CPB-29)`

**Independent Test**: `grep -rn "spin_lock\|spin-lock"` across `docs/`, `.specs/`, `data-plane/` and
`control-plane/app/` returns only historical/changelog references, none describing current behaviour.

---

### P2: Low-committed-rate advisory

**User Story**: As an admin sizing a plan, I want to be warned when a service's committed rate is too
small to survive the per-CPU split, so that I do not sell a committed tier that can never admit a
packet.

**Why P2**: A real consequence of `D-CPB-1`, but it degrades gracefully (traffic falls through to
burst rather than dropping), it affects only implausibly small plans on high-core nodes, and it is a
control-plane surface that can ship after the data-plane change.

**Acceptance Criteria**:

1. WHEN an admin sets `committed_clean_gbps` such that `committed_bytes_per_sec / node_cpu_count` is
   below a full MTU THEN the control plane SHALL surface a **warning** (not a block, matching the
   SRL-36 oversubscription precedent) explaining that the committed tier will be ineffective for
   full-size frames. `(CPB-30)`
2. WHEN the warning is raised THEN it SHALL name the node's CPU count and the computed per-core share
   so the admin can size around it. `(CPB-31)`

**Independent Test**: Create a plan with a tiny committed rate against a high-core node profile and
observe the warning; creation still succeeds.

---

### P3: Re-baseline the performance record

**User Story**: As an engineer planning A1–A4/B1/C2, I want the post-C1 numbers to become the new
reference, so subsequent optimisations are measured against reality.

**Acceptance Criteria**:

1. WHEN C1 is verified THEN `make -C data-plane bench` SHALL be re-run and §3 of the perf report
   updated (or annotated) with the new clean-accept figure. `(CPB-32)`
2. WHEN the record is updated THEN the `dp-load-benchmark` memory entry SHALL be updated to point at
   the contention harness and the new bottleneck ranking, since "spin-lock is the accept-path
   scaling bottleneck" will no longer be true. `(CPB-33)`

---

## Edge Cases

- WHEN `committed_bps / ncpus` rounds to 0 THEN `rl_burst()` floors the bucket depth at 1 **byte**,
  which admits no real frame — the committed tier is silently inert for that service. The system
  SHALL fall through to burst (no drop, no error) and the condition SHALL be documented (`CPB-26`)
  and warned about (`CPB-30`).
- WHEN traffic for one service is skewed onto a subset of cores (few flows, few sources) THEN those
  cores exhaust their share while other cores' committed tokens go unused; the service is demoted to
  burst below its aggregate committed rate. This is the accepted cost of `D-CPB-1` and SHALL NOT be
  treated as a defect.
- WHEN a config swap changes `committed_bps` THEN cores that have not yet seen a packet still hold
  the previous version's tokens; each resets lazily on its next packet (`CPB-11`) — the same
  eventual-consistency window burst/cap already have.
- WHEN `bpf_ktime_get_ns()` returns a value not greater than the bucket's `last_ns` (same-tick
  packets) THEN refill SHALL be skipped without corrupting `last_ns`, per `rl_refill_dim()`'s
  existing contract.
- WHEN the node's possible-CPU count differs from its online count THEN `rl_ncpus` keeps using
  **possible** CPUs as the loader already configures
  ([loader.c:646](../../../data-plane/loader/loader.c#L646)); the committed share is therefore
  conservative on partially-offline hosts, consistent with every other bucket.
- WHEN a packet's length exceeds the entire per-CPU committed burst THEN it can never be admitted
  from the committed tier regardless of idle time; it falls through to burst.
- WHEN the contention benchmark is run on a host with fewer cores than the largest requested `N`
  THEN it SHALL stop at the available count rather than oversubscribing cores and reporting
  meaningless contention.

---

## Requirement Traceability

| Requirement ID | Story | Phase | Status |
| --- | --- | --- | --- |
| CPB-01 | P1: Contention benchmark (N-thread, one service_id, aggregate Mpps) | Design | Pending |
| CPB-02 | P1: Contention benchmark (scaling efficiency column) | Design | Pending |
| CPB-03 | P1: Contention benchmark (budget seeded to avoid tier demotion) | Design | Pending |
| CPB-04 | P1: Contention benchmark (stability / reported spread) | Design | Pending |
| CPB-05 | P1: Contention benchmark (root/JIT preconditions) | Design | Pending |
| CPB-06 | P1: Contention benchmark (harness reuse + make target) | Design | Pending |
| CPB-07 | P1: Contention benchmark ("before" numbers recorded) | Design | Pending |
| CPB-08 | P2: Per-CPU bucket (`PERCPU_HASH` of `rl_bucket`) | Design | Pending |
| CPB-09 | P2: Per-CPU bucket (no lock, struct/assert/comment removed) | Design | Pending |
| CPB-10 | P2: Per-CPU bucket (per-core share via `rl_burst`/`rl_refill_dim`) | Design | Pending |
| CPB-11 | P2: Per-CPU bucket (lazy version reset) | Design | Pending |
| CPB-12 | P2: Per-CPU bucket (first-packet seed decides in-call) | Design | Pending |
| CPB-13 | P2: Per-CPU bucket (ladder fall-through + frozen reasons unchanged) | Design | Pending |
| CPB-14 | P2: Per-CPU bucket (`committed == 0` best-effort unchanged) | Design | Pending |
| CPB-15 | P2: Per-CPU bucket (no added map ops / ktime calls) | Design | Pending |
| CPB-16 | P2: Per-CPU bucket (measured scaling improvement, no single-core regression) | Design | Pending |
| CPB-17 | P3: Tests (per-CPU read helper) | Design | Pending |
| CPB-18 | P3: Tests (pinned runner + `test_no_refill` exactness) | Design | Pending |
| CPB-19 | P3: Tests (per-CPU map clear between cases) | Design | Pending |
| CPB-20 | P3: Tests (spin-lock probe removed, baseline re-pinned) | Design | Pending |
| CPB-21 | P3: Tests (`smoke_fairness.sh` committed rate scaled by CPU count) | Design | Pending |
| CPB-22 | P3: Tests (live smoke assertions still hold) | Design | Pending |
| CPB-23 | P3: Tests (M3 flood-A/starve-B gate still passes) | Design | Pending |
| CPB-24 | P4: Docs (all superseded rows amended in place) | Design | Pending |
| CPB-25 | P4: Docs (isolation vs delivery stated plainly) | Design | Pending |
| CPB-26 | P4: Docs (sub-MTU per-core share pathology quantified) | Design | Pending |
| CPB-27 | P4: Docs (TESTING.md fairness conventions) | Design | Pending |
| CPB-28 | P4: Docs (data-plane README map table) | Design | Pending |
| CPB-29 | P4: Docs (perf report §8.4/§8.6 marked done with numbers) | Design | Pending |
| CPB-30 | P2: Low-rate advisory (warning, not block) | - | Pending |
| CPB-31 | P2: Low-rate advisory (names CPU count + per-core share) | - | Pending |
| CPB-32 | P3: Re-baseline (`bench_dp` §3 refreshed) | - | Pending |
| CPB-33 | P3: Re-baseline (`dp-load-benchmark` memory updated) | - | Pending |

**ID format:** `CPB-[NUMBER]`
**Status values:** Pending → In Design → In Tasks → Implementing → Verified
**Coverage:** 33 total, 0 mapped to tasks, 33 unmapped ⚠️ (tasks phase not yet run)

---

## Success Criteria

- [ ] The contention benchmark exists and produces a **before** curve on locked code and an **after**
      curve on per-CPU code; the after curve shows materially better scaling efficiency at high core
      counts.
- [ ] `llvm-objdump`/`bpftool prog dump xlated` on the production object shows **zero** spin-lock
      helper calls.
- [ ] `bpftool map show` reports `svc_committed_state` as `percpu_hash`, `value_size` 32.
- [ ] `make -C data-plane test` green at the re-pinned baseline (137 today, minus the removed
      spin-lock test, plus any added cases).
- [ ] Privileged `smoke_fairness.sh`, `smoke_redirect.sh` and `smoke_apply.sh` pass as root on veth.
- [ ] `make -C data-plane bench` shows no single-core regression on `clean_redirect` versus the
      2026-07-23 median of ~620 ns (a small improvement is expected from dropping lock/unlock).
- [ ] Control-plane gate shows no new failures beyond the 6 pre-existing reds recorded in memory.
- [ ] `grep -rn "spin_lock" data-plane/src data-plane/tests` returns zero hits.
- [ ] No document still claims the committed bucket is exact or RSS-independent.

---

## Sizing

**Large** — one focused eBPF change, but it lands across the dp-unit suite, a privileged smoke
script, a new benchmark harness, and a **contractual requirement amendment** spanning PRD/TDD/
ROADMAP/two feature specs/TESTING/README, plus a P2 control-plane surface. Design and Tasks phases
are both required; do not skip to Execute.
