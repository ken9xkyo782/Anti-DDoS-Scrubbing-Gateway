# Drop-reason Counters Context

**Gathered:** 2026-07-08
**Spec:** `.specs/features/drop-reason-counters/spec.md`
**Status:** Ready for design

---

## Feature Boundary

Finalize the complete 16-reason TDD §9.2 drop-reason vocabulary in the shared `enum drop_reason` as a frozen index ABI; keep exact, lock-free per-CPU counting for every drop; add a rate-limited drop-event sampling channel (hard events/sec bound, safe with no reader); ship a small operator CLI for counter dump + sample tail. Out of scope: the 9 M3 drop paths themselves, per-service/billing traffic counters, `bloom_hit_lpm_miss`, worker aggregation (M4/M5), alerting (M6).

---

## Implementation Decisions

### Index numbering strategy (D-DRC-1)

- **Canonical ABI = §9.2 document order**, indices 0..15 exactly as listed in TDD §9.2. Concretely this is a one-move migration: `map_error` moves 4→15, `bogon_drop` takes 4; the four fail-fast reasons (0–3) and SLRD's `service_miss`/`service_disabled` (5/6) already coincide with §9.2 positions.
- **Single source of truth = name table in the header**: `drop_reason.h` carries a `drop_reason_name[]` string table adjacent to the enum; tests, the P3 CLI, and later the M4 worker decode from it. The human-readable index→name table added to docs (TESTING.md / data-plane README) explicitly references the header as authoritative.
- **Growth rule = append-only after 15**: post-v1/GA reasons take 16, 17, … in arrival order within `DROP_REASON_CAP = 32`; indices frozen by this feature never move again. Renumbering is legal only inside this feature, migrating the existing `BPF_PROG_TEST_RUN` suite in-place.
- **Sequencing = SLRD executes first** (ROADMAP order): service-lookup-redirect lands with its appended slots 5/6 (already §9.2-correct), then this feature performs the one-move renumber + freeze. No edits to SLRD's approved tasks.

### Agent's Discretion

User selected only index numbering for discussion; the following are design-time calls (fail-fast verification per project convention, not assumption):

- **Sampling mechanism & consumer** — ringbuf vs perf event array; who reads it in M2 (test-only vs CLI tail vs both); how much packet context an event carries beyond the spec minimum (DRC-09).
- **Rate-limit policy** — one global sample budget vs per-reason budgets; fixed constant vs runtime-tunable (map); concrete events/sec bound (DRC-10).
- **Ops CLI shape** — separate binary vs loader subcommand; one-shot dump vs watch/follow; output format (DRC-13/14).

---

## Specific References

- TDD §9.2 listing is the authoritative reason order and set (16 reasons).
- AD-015 deliberately kept indices append-stable and deferred final §10.2 numbering to this feature — that deferral is what D-DRC-1 now closes.
- TDD §11.1: ringbuf/perf **must** be sampled + rate-limited (mandatory performance condition) — constrains the sampling design regardless of mechanism chosen.
- AD-006: exactness posture — counters (money/SLA-adjacent) exact; sampling is telemetry-only.

---

## Deferred Ideas

None — discussion stayed within feature scope.
