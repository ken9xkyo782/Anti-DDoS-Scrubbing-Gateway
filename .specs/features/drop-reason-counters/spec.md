# Drop-reason Counters Specification

**Feature:** M2 #3 — Drop-reason counters
**Created:** 2026-07-08
**Status:** Verified — Execute complete (2026-07-08)
**Depends on:** Packet parse & fail-fast (VERIFIED); Service lookup & transparent redirect (VERIFIED)

## Problem Statement

The data-plane drops packets for many reasons, but today only 5 of the 16 standardized drop reasons (TDD §9.2/§10.2) exist in `enum drop_reason`, their numeric indices were assigned ad-hoc by implementation order, and there is no event-level visibility — only aggregate counts. M3 (policy enforcement) is about to add nine more drop paths, and M4/M5 (worker `TELEMETRY_AGGREGATE`, dashboards) will consume counter indices as a stable ABI. This is the last cheap moment to finalize the full reason vocabulary, freeze its numbering, and add the rate-limited sampling channel the TDD mandates (§11.1: ringbuf/perf must be sampled + rate-limited so it never clogs the hot path).

## Goals

- [x] Every §9.2 drop reason (16 total) defined in the shared `enum drop_reason` with a **frozen, documented index ABI** that M3–M5 build on without renumbering.
- [x] Every drop on the hot path counted **exactly** (per-CPU, lock-free) under its reason — counters trustworthy for diagnosis and, later, SLA/telemetry (AD-006 posture: exact for anything money/SLA touches).
- [x] A **rate-limited drop-event sampling channel** (ringbuf/perf) carrying per-drop context (reason + packet metadata) with a hard events/sec bound and zero hot-path blocking — the telemetry-event source M4/M5 will consume.
- [x] M3 features add a new drop path by calling one helper (`record_drop`-style) — no per-feature counter or sampling plumbing.

## Out of Scope

| Feature | Reason |
| --- | --- |
| Drop paths for the 9 M3 reasons (`bogon_drop`, `udp_amplification_drop`, `blacklist_drop`, `not_allowed`, `rate_limit_drop`, `service_ceiling_drop`, `congestion_drop`, `ingress_cap_drop`, `vip_ceiling_drop`) | M3 features own the checks; this feature ships their reason IDs + counter slots only (they read 0 until wired) |
| Per-service clean/drop traffic counters (PPS/BPS per service, billing byte counts) | Consumers are M3 fairness + M5 chargeback (AD-006); ROADMAP scopes this feature to drop **reasons**. Extending `counter_map` dimensions is additive later |
| `bloom_hit_lpm_miss` (bloom false-positive counter) | Needs the bloom filters — M3 blacklist feature |
| Worker `TELEMETRY_AGGREGATE` consumption of counters/samples | M4/M5; this feature defines the producer contract they read |
| Alerting on `map_error` > 0 etc. | M6 alerting |
| Sampled per-tenant drop-flow records for tenant self-service debug | OP-06, GA (Deferred Ideas) |

---

## User Stories

### P1: Standardized drop-reason contract ⭐ MVP

**User Story**: As a data-plane developer (and downstream telemetry consumer), I want the complete §9.2 drop-reason set defined in the single shared enum with frozen, documented indices, so that every current and future drop path, counter reader, and dashboard shares one stable vocabulary.

**Why P1**: Nine M3 drop paths and the M4/M5 telemetry ABI all hang off this enum; finalizing it after M3 starts means renumbering churn or a permanently ad-hoc ABI.

**Acceptance Criteria**:

1. WHEN `drop_reason.h` is compiled THEN it SHALL define exactly the 16 §9.2 reasons: `ipv6_unsupported`, `unsupported_ethertype`, `malformed_ipv4`, `fragment_unsupported`, `bogon_drop`, `service_miss`, `service_disabled`, `udp_amplification_drop`, `blacklist_drop`, `not_allowed`, `rate_limit_drop`, `service_ceiling_drop`, `congestion_drop`, `ingress_cap_drop`, `vip_ceiling_drop`, `map_error`.
2. WHEN the enum is finalized THEN each reason's numeric index SHALL be documented as a frozen index→name ABI table (in the header and TESTING/`README` convention docs), all within `DROP_REASON_CAP = 32` headroom.
3. WHEN the already-executed drop paths run (4 fail-fast reasons, `map_error`, and — once service-lookup-redirect executes — `service_miss`/`service_disabled`) THEN they SHALL record under the finalized indices, with the existing `BPF_PROG_TEST_RUN` regression suite updated and green.
4. WHEN a reason has no drop path yet (the 9 M3 reasons) THEN its counter slot SHALL exist and read 0.

**Independent Test**: Build the BPF object; dump the enum/ABI table; run the existing test suite — every historical drop test passes against the finalized indices, and reading all 16 counter slots succeeds (unwired ones = 0).

---

### P1: Exact per-CPU drop counters ⭐ MVP

**User Story**: As an admin/operator, I want every dropped packet counted exactly under its reason on the hot path, so that drop-reason distribution is trustworthy for attack diagnosis now and for SLA/telemetry later.

**Why P1**: "Per-CPU counters populated / drop reasons correct" is the M2 milestone exit criterion; exactness (vs sampling) is an architectural invariant (AD-006).

**Acceptance Criteria**:

1. WHEN a packet is dropped for reason R THEN the data-plane SHALL increment `counter_map[R]` on the executing CPU by exactly 1 — lock-free, no sampling, no loss.
2. WHEN counters are read from userspace THEN per-CPU values SHALL aggregate into exact node totals per reason.
3. WHEN the drop helper is invoked with an out-of-range/unknown reason THEN the packet SHALL still be dropped (fail-closed) AND the anomaly SHALL be accounted under `map_error`.
4. WHEN the XDP program is reloaded/re-attached THEN counters SHALL restart from zero, and this reset-on-reload semantic SHALL be documented for consumers (M4/M5 compute deltas, not lifetime totals).

**Independent Test**: Inject N synthetic packets per drop reason via `BPF_PROG_TEST_RUN`; userspace aggregation reads exactly N for each reason and 0 elsewhere.

---

### P2: Rate-limited drop-event sampling

**User Story**: As a telemetry consumer (M4 worker, M5 dashboards — and an operator debugging today), I want a rate-limited stream of sampled drop events carrying the reason plus packet context, so that event-level detail (who/what was dropped) exists without unbounded hot-path cost.

**Why P2**: ROADMAP scopes sampling into this feature and §11.1 makes rate-limiting mandatory, but counters alone satisfy the M2 milestone exit; sampling is the should-have half.

**Acceptance Criteria**:

1. WHEN a packet is dropped AND the sampling budget permits THEN the data-plane SHALL emit one event containing at least: drop reason, src/dst IPv4, IP protocol, L4 ports (when parsed), and `service_id` (when known).
2. WHEN the drop rate exceeds the sampling budget THEN excess events SHALL be suppressed with no hot-path blocking or added per-packet latency, and counters SHALL remain exact regardless.
3. WHEN no userspace consumer is reading (buffer full) THEN packet processing SHALL be unaffected and lost/suppressed samples SHALL be observable via a counter.
4. WHEN drops occur at line rate THEN emitted events SHALL never exceed the configured budget (bounded events/sec — bound value is a Design/context decision).

**Independent Test**: Fire a burst of M drops with budget B < M; a userspace reader receives ≤ B events with correct fields, the suppressed count accounts for the remainder, and `counter_map` still shows exactly M.

---

### P3: Operator counter & sample visibility

**User Story**: As an admin/operator, I want a small userspace tool to dump per-reason totals and tail sampled drop events human-readably, so that I can verify and diagnose the data-plane before M5 dashboards exist.

**Why P3**: Tests already read maps programmatically; a CLI is operator convenience, and M5 replaces it for tenants.

**Acceptance Criteria**:

1. WHEN the operator runs the counter dump THEN it SHALL print each reason's name, index, and CPU-aggregated total.
2. WHEN the operator tails samples THEN each event SHALL print human-readably (reason name, addresses, ports, service where known).

**Independent Test**: With traffic injected, run the tool and visually confirm totals match test-injected counts and events decode correctly.

---

## Edge Cases

- WHEN multiple CPUs drop packets for the same reason concurrently THEN per-CPU slots SHALL keep counts exact with no cross-CPU contention (no shared-cacheline atomics on the hot path).
- WHEN the sampling buffer cannot accept an event (full / reservation failure) THEN only the sample is lost — the drop verdict, the exact counter increment, and packet latency are unaffected.
- WHEN a counter value approaches `u64` range THEN wraparound is documented as practically unreachable and not handled specially.
- WHEN the program runs without any userspace reader attached (normal M2 state — no worker until M4) THEN sampling SHALL be safe to leave enabled (bounded memory, no leak, no stall).
- WHEN this feature executes before service-lookup-redirect does THEN `service_miss`/`service_disabled` remain enum+counter-only (no path to exercise) — the suite SHALL NOT fabricate assertions for unwired reasons.

---

## Dependencies & Sequencing

- **Packet parse & fail-fast** — VERIFIED. Supplies `drop_reason.h`, `counter_map`, `record_drop`, the `BPF_PROG_TEST_RUN` harness this feature extends.
- **Service lookup & transparent redirect** — tasks approved, Execute deferred. It appends `DR_SERVICE_MISS`/`DR_SERVICE_DISABLED` and adds `pkt_meta.service_id`. **Intended order (ROADMAP): SLRD executes first.** If order flips, this feature still lands the full 16-reason ABI; only the two service reasons' live assertions and `service_id` in samples wait for SLRD.
- **Index freeze is one-way**: after this feature, indices are ABI — M3+ append within `DROP_REASON_CAP` headroom only. Renumbering existing indices (if the canonical order differs from current append order) is allowed **only within this feature**, migrating the existing suite in-place.

---

## Requirement Traceability

| Requirement ID | Story | Phase | Status |
| --- | --- | --- | --- |
| DRC-01 | P1: Contract — exactly the 16 §9.2 reasons defined | Execute | Verified |
| DRC-02 | P1: Contract — frozen index→name ABI documented, within CAP=32 | Execute | Verified |
| DRC-03 | P1: Contract — existing drop paths on finalized indices, suite migrated & green | Execute | Verified |
| DRC-04 | P1: Contract — unwired M3 reasons present, counters read 0 | Execute | Verified |
| DRC-05 | P1: Counters — exact per-CPU increment per drop (lock-free) | Execute | Verified |
| DRC-06 | P1: Counters — userspace per-reason aggregation across CPUs | Execute | Verified |
| DRC-07 | P1: Counters — out-of-range reason: fail-closed drop + `map_error` accounting | Execute | Verified |
| DRC-08 | P1: Counters — reset-on-reload semantics documented for consumers | Execute | Verified |
| DRC-09 | P2: Sampling — event carries reason + src/dst/proto/ports/service_id-when-known | Execute | Verified |
| DRC-10 | P2: Sampling — hard events/sec budget; excess suppressed, non-blocking | Execute | Verified |
| DRC-11 | P2: Sampling — safe with no reader; lost/suppressed samples observable | Execute | Verified |
| DRC-12 | P2: Sampling — counters exact independent of sampling state | Execute | Verified |
| DRC-13 | P3: Visibility — CLI counter dump (name, index, aggregated total) | Execute | Verified |
| DRC-14 | P3: Visibility — CLI sample tail, human-readable decode | Execute | Verified |
| DRC-15 | Cross-cutting — single-helper API: new drop path = one call, no plumbing | Execute | Verified |
| DRC-16 | Cross-cutting — tests per reason via `BPF_PROG_TEST_RUN`; sampling budget test | Execute | Verified |
| DRC-17 | Cross-cutting — TESTING.md/data-plane docs updated (ABI table + sampling conventions) | Execute | Verified |

**ID format:** `DRC-[NUMBER]`
**Status values:** Pending → In Design → In Tasks → Implementing → Verified
**Coverage:** 17 total, 17 mapped to tasks (T1–T6, `tasks.md`), 0 unmapped ✅

---

## Success Criteria

- [x] All 16 §9.2 reasons enumerated, counted, and published as a frozen index ABI; `DROP_REASON_CAP` headroom ≥ 16 remaining for GA additions.
- [x] Existing data-plane test suite green after any index migration; every wired reason has an exact-count test (inject N ⇒ read N).
- [x] Under a synthetic drop flood with budget B, the sample stream never exceeds B events/sec, suppression is observable, and counters stay exact — verified by test, not inspection.
- [x] An M3 feature can add a drop path with a single helper call and zero changes to counter/sampling infrastructure.
