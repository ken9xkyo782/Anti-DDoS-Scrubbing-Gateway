# Per-CPU Committed Bucket — Discussion Context

Gray areas resolved with the user before the spec was written (2026-07-23). Each decision is
referenced from [spec.md](spec.md) as `D-CPB-n`.

---

## D-CPB-1 — Accuracy model: pure per-CPU split

**Question**: C1 contradicts `FAIR-05` (committed is "accounted exactly … independent of RSS/CPU
distribution") and the CM-04 "hard guarantee" framing, which is tied to billing
(`billed = max(committed, p95)`) and to the "committed honored" alerting/SLA dimension. Which
accuracy model do we commit to?

**Options considered**:

| Option | Trade-off |
| --- | --- |
| **Pure per-CPU split** (chosen) | Each core gets `committed_bps/ncpus` via `rl_burst`/`rl_refill_dim`. Fully lock-free, one bucket implementation across the whole data plane, smallest reviewable diff. Under RSS skew a service can be demoted to burst before reaching its aggregate committed rate. |
| Per-CPU + cold global spill | Lock-free per-core fast path; take the spin-lock on a shared remainder bucket only when a core's share is empty. Preserves aggregate exactness, keeps the lock in the program, ~30 extra lines. |
| Per-CPU with MTU floor | Per-core depth floored at a few MTUs so tiny plans keep a usable committed tier. Over-grants `Σ` per-core depth versus the configured rate at low rates. |

**Decision**: **Pure per-CPU split.**

**Rationale**: it is what C1 proposes, it matches the precedent every other bucket in the data plane
already follows (ingress-cap, burst, node headroom, VIP ceiling, per-rule), and per-CPU split was
already recorded as the sanctioned fallback for the committed bucket in AD-025. The exactness that is
being given up buys nothing structural: **per-service isolation** — flood A never touches B's budget,
the actual M3 gate and the actual CM-04 promise — comes from the buckets being keyed per service, not
from the lock.

**Consequence**: `FAIR-05` and TDD §4.4/§13 must be amended, not quietly outgrown. The spec carries a
dedicated [Superseded Requirements](spec.md#superseded-requirements) table for this, and story P4
makes the restatement a P1 deliverable rather than a docs afterthought.

**Known pathology accepted**: when `committed_bps / ncpus` falls below one MTU the committed tier is
inert for full-size frames on every core. Traffic falls through to burst (no drop). Mitigated by
documentation (`CPB-26`) and a P2 control-plane warning (`CPB-30`).

---

## D-CPB-2 — Verification: add a multi-CPU contention benchmark

**Question**: today's [bench_dp](../../../data-plane/tests/bench_dp.c) pins to CPU 0, where an
uncontended `bpf_spin_lock` costs almost nothing. How do we prove C1 works?

**Options considered**: reuse single-CPU `bench_dp` only (~20–40 ns uncontended delta, headline claim
unverified) · **add an N-thread contention benchmark** (chosen) · do D1 (production-object baseline)
first, then the contention benchmark.

**Decision**: **Add the multi-CPU contention benchmark**, and run it against the current locked code
*before* the change so a real "before" curve exists.

**Rationale**: the entire value of C1 is multi-core scaling of the accept path for one hot VIP. A
measurement that cannot observe contention cannot confirm or refute it, and "tests still pass" is not
a performance result. D1 was deliberately not made a prerequisite: it changes the absolute numbers,
not the shape of the scaling curve, and gating C1 on it would grow the scope without improving the
comparison.

**Shape**: N threads, each pinned to a distinct CPU, each running `BPF_PROG_TEST_RUN` on the
`clean_redirect` scenario against **the same** `service_id`; report aggregate Mpps plus scaling
efficiency for `N ∈ {1, 2, 4, 8, 16, …}`.

---

## D-CPB-3 — Retire the spin-lock test probe

**Question**: `fair_test_spin_lock_mutate()` ([fairness.h:118](../../../data-plane/src/fairness.h#L118)),
`FAIR_TEST_TRIGGER_SPIN_LOCK`, `test_fair_spin_lock_probe()`
([xdp_gateway.bpf.c:120](../../../data-plane/src/xdp_gateway.bpf.c#L120)) and
`test_fair_committed_spin_lock_mutates_tokens` exist to de-risk spin-lock-in-XDP (FAIR-22). Keep or
remove?

**Decision**: **Remove them.**

**Rationale**: once nothing in the program locks, the probe asserts a kernel capability the product no
longer uses. Keeping it would mean retaining a locked map and test-only branches in the production
object to preserve an escape hatch for a decision being made deliberately.

**Consequence**: the dp-unit baseline drops by one test and must be re-pinned at Execute; FAIR-22's
de-risk is retired.

---

## D-CPB-4 — Reuse `struct rl_bucket`

**Question**: what replaces `struct fair_committed_bucket` (24 B, holds the lock, has a
`_Static_assert` map contract at [fairness.h:50](../../../data-plane/src/fairness.h#L50))?

**Decision**: **Reuse the existing 32 B `struct rl_bucket`**, exactly as `svc_burst_state` does.

**Rationale**: `fair_committed_admit()` becomes a near-clone of `fair_burst_admit()` with
`committed_bps` in place of `burst_bps`, reusing `fair_bps_bucket_reset/refill/consume` verbatim. That
deletes the bespoke ~50-line refill arithmetic that today duplicates `rl_refill_dim()` and leaves one
bucket implementation in the data plane instead of two. The alternative — a new lockless struct — has
a smaller test/doc surface but keeps a second implementation alive for no functional gain.

**Consequence**: `read_fair_committed_bucket()` moves to the `read_fair_burst_bucket_cpu0()` per-CPU
pattern; the committed-bucket `_Static_assert` and the "top-level HASH is required" comment are
deleted.

---

## Notes carried into design

- `rl_burst(rate, ncpus, test_no_refill)` returns the **undivided** rate when `test_no_refill` is set
  ([rules.h:150](../../../data-plane/src/rules.h#L150)), so the deterministic dp-unit committed
  quotas stay exact under the CPU-pinned runner — no test arithmetic needs rescaling.
- [smoke_fairness.sh:27](../../../data-plane/tests/smoke_fairness.sh#L27) seeds
  `COMMITTED_BPS=$((FRAME_LEN * 2))` = 120 B/s. Divided across 96 possible CPUs this floors to 1 byte
  and admits **zero** 60-byte frames from the committed tier. The smoke will break unless the rate is
  scaled by `POSSIBLE_CPUS` — it already scales `CEILING_BPS` that way. This is the sharpest concrete
  instance of the D-CPB-1 pathology and is tracked as `CPB-21`.
- The loader's default `DEFAULT_FAIR_COMMITTED_BPS` = 12.5 GB/s
  ([loader.c:54](../../../data-plane/loader/loader.c#L54)) gives ~130 MB/s per core at 96 CPUs — the
  default seed is unaffected.
- The apply wire contract and `struct fair_config` are **unchanged**; C1 touches runtime bucket
  storage only, so no control-plane version bump is involved.
