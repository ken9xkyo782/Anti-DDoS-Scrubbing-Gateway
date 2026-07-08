# Drop-reason Counters — Tasks

**Design:** `.specs/features/drop-reason-counters/design.md` (AD-017)
**Spec:** `.specs/features/drop-reason-counters/spec.md` (DRC-01..17)
**Status:** **VERIFIED / EXECUTED (2026-07-08)** — T1–T6 complete and committed; final quick gate passed
(`make test` → 34 dp-unit tests). Build/tool gate passed (`make bpf skel loader dpstat`).
**Execute tooling:** Skill `coding-guidelines` on T1–T5 (C/XDP + tests + tools), none for T6; no MCPs; execution mode = inline (per recorded preference)
**Prerequisite:** service-lookup-redirect **executed** (D-DRC-1d). Baseline suite count **B=29** at T1
start; final suite count **34** (B+5), no deletions.

---

## Execution Plan

### Phase 1 — ABI + sampling core (sequential; shared `drop_reason.h` / `xdp_gateway.bpf.c` / `tests/`)

```
T1 → T2
```

### Phase 2 — verification + pinning (parallel: disjoint files)

```
T2 ──┬→ T3        (tests/test_parse.c + PKT_TEST_HOOKS edit in xdp_gateway.bpf.c)
     └→ T4 [P]    (loader/loader.c only)
```

### Phase 3 — CLI, then docs (sequential)

```
T3, T4 → T5 → T6
```

---

## Task Breakdown

### T1: Freeze the drop-reason ABI + event contract headers

**Status:** Complete — verified 2026-07-08 (`make test` → 30 passed)
**What**: Rewrite `drop_reason.h` with the final §9.2 enum (0..15, explicit values, `DROP_REASON_COUNT=16`, `_Static_assert(COUNT<=CAP)`, `/* FROZEN ABI — append only */` banner, userspace `drop_reason_name[]` under `#ifndef __BPF__`); create `drop_event.h` (`struct drop_event` 32 B, `struct sample_config`, `enum sample_stat`). Add the DRC-04 test case (after corpus: all 16 slots readable, unwired M3 reasons == 0).
**Where**: `data-plane/src/drop_reason.h` (rewrite), `data-plane/src/drop_event.h` (new), `data-plane/tests/test_parse.c` (loop bound → `DROP_REASON_COUNT`; +1 case)
**Depends on**: None
**Reuses**: existing `counter_map`/`record_drop` shape (semantics untouched in T1); §9.2 strings verbatim; suite's per-case counter-reset pattern
**Requirement**: DRC-01, DRC-02, DRC-03, DRC-04

**Tools**: MCP: NONE · Skill: `coding-guidelines`

**Done when**:
- [x] Enum matches design table exactly (only `map_error` moves 4→15; `bogon_drop`=4; SLRD's 5/6 unchanged)
- [x] `drop_reason_name[DROP_REASON_COUNT]` index-aligned; static assert compiles
- [x] `drop_event.h` compiles standalone in both BPF and userspace TUs (`<linux/types.h>` only)
- [x] Migration proof: existing suite passes **unmodified in expectations** (symbols only, no hardcoded indices found/left)
- [x] Gate check passes: `make test`
- [x] Test count: **B + 1** pass (no silent deletions)

**Tests**: dp-unit
**Gate**: quick

**Verify**: `cd data-plane && make test` → `B+1 passed`; `grep -n "= 4\|= 15" src/drop_reason.h` shows `bogon_drop`/`map_error` placement.

**Commit**: `feat(drop-counters): freeze 16-reason drop ABI (§9.2 order) + drop_event contract`

---

### T2: Sampling core — ringbuf, token bucket, fused `record_drop(meta, reason)`

**Status:** Complete — verified 2026-07-08 (`make test` → 30 passed)
**What**: New `sample.h` (maps `drop_ringbuf` 256 KiB / `sample_config` / `sample_bucket` / `sample_stats` + `sample_drop()` per design); change `record_drop` to `record_drop(const struct pkt_meta *meta, enum drop_reason r)` with out-of-range clamp → `DR_MAP_ERROR`, exact count, then `sample_drop`; mechanically update every call site.
**Where**: `data-plane/src/sample.h` (new), `data-plane/src/drop_reason.h` (helper), `data-plane/src/xdp_gateway.bpf.c` (call sites)
**Depends on**: T1
**Reuses**: `pkt_meta` fields (read-only); verified ringbuf/ktime semantics (design Research notes); per-CPU no-shared-atomics posture of `counter_map`
**Requirement**: DRC-05, DRC-06, DRC-07 (impl), DRC-10 (impl), DRC-12, DRC-15

**Tools**: MCP: NONE · Skill: `coding-guidelines`

**Done when**:
- [x] Verifier accepts the program with all four new maps (proven by `make test` loading it)
- [x] With `sample_config` unset (zeroed → rate 0, burst 0) sampling is inert: existing suite green, counters exact — **no expectation changes**
- [x] `record_drop` is the only drop entry point (no direct `counter_map` writes at call sites)
- [x] Gate check passes: `make test`
- [x] Test count: **B + 1** pass (unchanged from T1; behavior assertions land in T3 with the consumer harness — merge-forward per TESTING.md, not deferral: unrunnable without T3's consumer)

**Tests**: dp-unit (regression; new sampling assertions merge forward into T3)
**Gate**: quick

**Verify**: `cd data-plane && make test` → `B+1 passed`; `bpftool` on the test object (or skeleton inspection) shows `drop_ringbuf` type `ringbuf`, `max_entries 262144`.

**Commit**: `feat(drop-counters): ringbuf sampling core + per-CPU token bucket behind record_drop`

---

### T3: Ringbuf de-risk + sampling test cases

**Status:** Complete — verified 2026-07-08 (`make test` → 34 passed; test_run→ringbuf delivery succeeded)
**What**: Extend the test harness with a `ring_buffer__new`/`consume` consumer over the skeleton's `drop_ringbuf` fd. **First case = de-risk** (config `rate=0,burst=1`; 1 drop via test_run; consume must deliver exactly 1 event) — if it fails, switch suite to the documented stats-only fallback (assert `sample_stats` instead of consuming events; event content moves to `make smoke`) and record the finding in STATE Lessons. Then: budget-bound case (`burst=B_s`, fire `M>B_s` drops → exactly `B_s` emitted, `M−B_s` suppressed, `counter_map` exactly `M`); event-content case (fields match injected frame + reason; `service_id` when known); fail-closed case (out-of-range reason via a `-DPKT_TEST_HOOKS`-gated trigger → `map_error`++, packet dropped).
**Where**: `data-plane/tests/test_parse.c` (+consumer, +4 cases), `data-plane/src/xdp_gateway.bpf.c` (test-hook trigger under `PKT_TEST_HOOKS` only)
**Depends on**: T2
**Reuses**: harness env + `pkt_build.h` frames; `test_meta_map` hook pattern; determinism convention `rate=0, burst=B_s` (design)
**Requirement**: DRC-07 (assert), DRC-09, DRC-10 (assert), DRC-11, DRC-12 (assert), DRC-16

**Tools**: MCP: NONE · Skill: `coding-guidelines`

**Done when**:
- [x] De-risk verdict recorded (events consumable after test_run: yes/no + fallback applied if no)
- [x] Budget, content, and fail-closed cases pass with exact counts (no timing dependence)
- [x] Suppressed/lost observable via `sample_stats` assertions
- [x] Gate check passes: `make test`
- [x] Test count: **B + 5** pass (no silent deletions)

**Tests**: dp-unit
**Gate**: quick

**Verify**: `cd data-plane && make test` → `B+5 passed`, de-risk case name visible in output.

**Commit**: `test(drop-counters): ringbuf de-risk + budget/content/fail-closed sampling cases`

---

### T4: Loader — pin observability maps + seed sampling defaults [P]

**Status:** Complete — verified 2026-07-08 (`make bpf skel loader`)
**What**: Extend the loader: `bpf_map__set_pin_path()` for `counter_map`, `drop_ringbuf`, `sample_config`, `sample_stats` under `/sys/fs/bpf/xdp_gateway/` before load (fail-loud if pin dir/entries already exist); after load, write default `sample_config` (rate 256/s, burst 64 per CPU); unpin all on signal detach.
**Where**: `data-plane/loader/loader.c`
**Depends on**: T2 (maps exist)
**Reuses**: existing skeleton lifecycle + signal teardown; fail-loud posture (D-PKT-1)
**Requirement**: DRC-13/14 enabler (pinned access contract)

**Tools**: MCP: NONE · Skill: `coding-guidelines`

**Done when**:
- [x] Loader builds; pin paths + default seeding implemented exactly as design table (4 pinned, `sample_bucket` not pinned)
- [x] Stale-pin case errors with a clear message (no silent reuse)
- [x] Gate check passes: `make bpf skel loader`

**Tests**: none (loader layer — build gate + manual smoke per TESTING.md)
**Gate**: build

**Verify**: build passes; manual (privileged, optional now, exercised in `make smoke` runs): run loader on veth pair → `ls /sys/fs/bpf/xdp_gateway/` shows the 4 maps; Ctrl-C → dir empty.

**Commit**: `feat(drop-counters): pin observability maps + seed sample_config in loader`

---

### T5: `dpstat` operator CLI

**Status:** Complete — verified 2026-07-08 (`make bpf skel loader dpstat`; no-gateway error path checked)
**What**: New `tools/dpstat.c` + `make dpstat` target: `dpstat counters [-w <sec>]` (per-CPU-aggregated `index name total` rows from pinned `counter_map`, using `drop_reason_name[]`, + emitted/suppressed/lost footer), `dpstat tail` (ring_buffer poll on pinned `drop_ringbuf`, human-readable decode until SIGINT), `dpstat rate <per_cpu_rate> <burst>` (write pinned `sample_config`). Clear "gateway not loaded / maps not pinned" error when `bpf_obj_get` fails.
**Where**: `data-plane/tools/dpstat.c` (new), `data-plane/Makefile` (target)
**Depends on**: T1 (name table), T2 (map/event contracts), T4 (pin-path contract)
**Reuses**: `drop_reason_name[]`, `drop_event.h`, verified `ring_buffer__*` API, pinned paths from T4
**Requirement**: DRC-13, DRC-14

**Tools**: MCP: NONE · Skill: `coding-guidelines`

**Done when**:
- [x] All three subcommands implemented per design; no duplicated name strings (header table only)
- [x] Builds clean via `make dpstat`; no-gateway error path prints the friendly message (testable unprivileged: run without pins)
- [x] Gate check passes: `make bpf skel loader dpstat`

**Tests**: none (ops tool — no dp-unit layer; behavior over pinned maps exercised in privileged smoke/manual verify)
**Gate**: build

**Verify**: `./build/dpstat counters` without a loaded gateway → friendly error, exit ≠ 0. Privileged (optional now): loader on veth + `dpstat counters` shows 16 zeroed rows; `dpstat rate 0 5` then replayed drops → `tail` prints ≤5 events.

**Commit**: `feat(drop-counters): dpstat CLI — counters dump, sample tail, rate tuning`

---

### T6: Documentation — ABI table, semantics, conventions

**Status:** Complete — verified 2026-07-08 (`make test` → 34 passed)
**What**: `TESTING.md` data-plane section: frozen index→name ABI table (referencing `drop_reason.h` as authoritative), sampling determinism convention (`rate=0,burst=B_s`), updated corpus/count note, de-risk outcome (incl. fallback status if triggered). `data-plane/README.md`: pin paths, `dpstat` usage, reset-on-reload semantics (consumers compute deltas), per-CPU budget semantics (node bound = rate × CPUs), append-only growth rule.
**Where**: `.specs/codebase/TESTING.md`, `data-plane/README.md`
**Depends on**: T3, T4, T5 (documents outcomes, not intentions)
**Reuses**: existing TESTING.md data-plane section structure (A-PKT-2 / A-SLRD-8 pattern)
**Requirement**: DRC-02 (doc table), DRC-08, DRC-17

**Tools**: MCP: NONE · Skill: NONE

**Done when**:
- [x] ABI table index-identical to `drop_reason.h`; header cited as source of truth
- [x] Reset-on-reload + budget semantics documented where M4/M5 will look (README) and testing conventions in TESTING.md
- [x] Gate check passes: `make test` (docs-only; proves no accidental code drift)

**Tests**: none (docs)
**Gate**: quick (regression re-run)

**Verify**: rendered tables match `drop_reason_name[]` order; `make test` → `B+5 passed`.

**Commit**: `docs(drop-counters): drop-reason ABI table + sampling/reset semantics`

---

## Parallel Execution Map

```
Phase 1 (sequential):        T1 ──→ T2
Phase 2 (parallel after T2): ├── T3        (tests + PKT_TEST_HOOKS)
                             └── T4 [P]    (loader only — disjoint files, no tests)
Phase 3 (sequential):        T3+T4 ──→ T5 ──→ T6
```

Only **T4** is `[P]`: it touches only `loader/loader.c`, has no test execution, and shares no files
with T3. T1→T2→T3 serialize on `drop_reason.h`/`xdp_gateway.bpf.c`/`test_parse.c`. T5 serializes
after both (needs T4's pin contract; edits `Makefile`). T6 documents outcomes. dp-unit is
parallel-safe as infrastructure, but per TESTING.md tasks editing shared parser/test files serialize —
exactly T1/T2/T3.

---

## Pre-approval Check 1 — Task Granularity

| Task | Scope | Status |
| --- | --- | --- |
| T1 | 1 contract rewrite + 1 new header + 1 test case (one concept: the ABI) | ✅ Granular |
| T2 | 1 new header + 1 helper signature + mechanical call sites (one concept: sampling core) | ✅ Granular (cohesive) |
| T3 | 1 test-file extension + 1 gated hook (one concept: sampling verification) | ✅ Granular |
| T4 | 1 file (loader) | ✅ Granular |
| T5 | 1 new tool + 1 Makefile target | ✅ Granular |
| T6 | 2 doc files, one topic | ✅ Granular |

## Pre-approval Check 2 — Diagram-Definition Cross-Check

| Task | Depends On (body) | Diagram Shows | Status |
| --- | --- | --- | --- |
| T1 | None | start of Phase 1 | ✅ Match |
| T2 | T1 | T1 → T2 | ✅ Match |
| T3 | T2 | T2 → T3 | ✅ Match |
| T4 | T2 | T2 → T4 | ✅ Match |
| T5 | T1, T2, T4 (+T3 phase-ordering) | T3+T4 → T5 (T1/T2 transitive) | ✅ Match |
| T6 | T3, T4, T5 | T5 → T6 (T3/T4 transitive) | ✅ Match |

T3 and T4 are the only same-phase pair; they do not depend on each other and share no files. ✅

## Pre-approval Check 3 — Test Co-location Validation

| Task | Code Layer Created/Modified | Matrix Requires | Task Says | Status |
| --- | --- | --- | --- | --- |
| T1 | XDP contracts + verdict-adjacent (dp-unit layer) | dp-unit | dp-unit (B+1) | ✅ OK |
| T2 | XDP program behavior (dp-unit layer) | dp-unit | dp-unit regression; new assertions merged **forward** into T3 (unrunnable without T3's consumer — TESTING.md merge-forward rule, not deferral) | ✅ OK |
| T3 | test harness + gated hook | dp-unit | dp-unit (B+5) | ✅ OK |
| T4 | loader | none (build gate + manual/smoke per TESTING.md) | none / build | ✅ OK |
| T5 | ops tool (no matrix layer) | none | none / build + manual verify | ✅ OK |
| T6 | docs | none | none / quick regression | ✅ OK |

---

## Requirement Coverage (tasks → spec)

| DRC | Tasks | | DRC | Tasks |
| --- | --- | --- | --- | --- |
| 01 | T1 | | 10 | T2 (impl), T3 (assert) |
| 02 | T1, T6 | | 11 | T3 |
| 03 | T1 | | 12 | T2 (impl), T3 (assert) |
| 04 | T1 | | 13 | T4 (enabler), T5 |
| 05 | T2 | | 14 | T5 |
| 06 | T2/T3 (aggregation asserts) | | 15 | T2 |
| 07 | T2 (impl), T3 (assert) | | 16 | T3 |
| 08 | T6 | | 17 | T6 |
| 09 | T3 | | | |

**Coverage:** 17 total, 17 mapped to tasks, 0 unmapped ✅
