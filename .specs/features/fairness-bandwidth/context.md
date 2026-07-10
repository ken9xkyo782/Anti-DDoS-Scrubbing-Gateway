# Fairness & Bandwidth Reservation (§8.4) — Discuss Context

**Feature:** `.specs/features/fairness-bandwidth/` (FAIR-01..27)
**Date:** 2026-07-10
**Spec status:** APPROVED as drafted (2026-07-10)
**Gray areas resolved:** D-FAIR-1 (GA-1), D-FAIR-2 (GA-2) — recorded in STATE.md as **AD-024**

---

## D-FAIR-1: Ingress-cost cap = dual-dimension (bps + derived pps), k = 3 default

**Decision (GA-1, option a):** the per-service ingress-cost cap enforces **both dimensions**:
byte budget = `k × ceiling_clean_gbps` (bytes/sec) directly; packet budget =
`k × ceiling ÷ reference_packet_size`, with the reference packet size a **documented, node-tunable
constant** (order of ~512 B; exact default confirmed at Design). Default **k = 3** (inside the
TDD's k≈2–4 band), node-tunable at load (AD-019 `rl_ncpus` rodata posture, FAIR-15) — never
per-packet or per-service configurable in v1.

**Why:** the cap's stated purpose (TDD §4.4 mechanism 3) is bounding the **CPU** a flooded
service consumes before the expensive lookups — and pps, not bps, is the CPU cost dimension. A
bps-only cap is nearly blind to small-packet floods (10 Gbps ceiling × k=3 admits ~44 Mpps of
64 B classification work — more than the whole node's 20 Mpps target). The dual bucket reuses
ARL's `pps/bps` token shape verbatim, so the cost of the second dimension is negligible.

**Consequences / how to apply:**
- FAIR-15: cap bucket carries pps + bps tokens; either dimension exhausting drops
  `ingress_cap_drop` (index 13).
- The derived-pps rule (`k × ceiling ÷ ref_size`) and both knobs (`k`, `ref_size`) are documented
  in FAIR-27's node-config docs; Design fixes the exact ref-size default and its rodata/env
  delivery.
- FAIR-24's fairness scenario floods with small packets so the pps dimension (the interesting
  one) is what the gate exercises.

**Trade-off accepted:** the reference packet size is a heuristic — services with legitimately
tiny average packets (high-pps, low-bps workloads) hit the pps cap earlier than `k × ceiling`
bytes would suggest; mitigable per node via the ref-size/k knobs, documented rather than
per-service-configurable in v1.

---

## D-FAIR-2: `node_clean_capacity` = env-driven seed value, documented 40 Gbps default when unset

**Decision (GA-2, option a):** v1 delivers `node_clean_capacity` via an **env-driven seed/loader
value** (`XDPGW_`-style env, exact name at Design — same D-SLRD-1 posture as the WLV/BLK seeds);
the seed helper (v1) and the M4 worker (authoritative) compute the node headroom rate as
`node_clean_capacity − Σ committed_clean_gbps`, floored at 0, recomputed whenever plan changes
shift `Σ committed`. **Unset ⇒ documented default of 40 Gbps** (the §15 node target). The loader
never fails on an unset capacity.

**Why:** the blast radius of a mis-set capacity is **burst QoS only** — committed traffic skips
the node bucket entirely (FAIR-10/11) and §7.2 keeps `Σ committed ≤ capacity` control-plane-warned
(SRL-36), so the hard guarantee never depends on this value. That makes the dev/baseline-friendly
default the right trade: every M2/M3 feature has kept the "default seed just works" posture for
the quick suite and smoke, and a fail-loud mandatory env would break that for the lowest-stakes
config value in the feature.

**Consequences / how to apply:**
- FAIR-12: headroom derivation (`capacity − Σ committed`, floor 0) documented; env + default in
  FAIR-27's docs alongside `k`/`ref_size`.
- FAIR-23: default seed uses the 40 Gbps default — generous plans + ample headroom keep the
  post-BLK baseline verdict-identical.
- FAIR-13: oversubscription (warned, not blocked) ⇒ headroom rate 0 ⇒ all burst sheds
  `congestion_drop` — the documented consequence stands regardless of how capacity was delivered.
- M4 contract note: the worker owns recomputing the headroom rate on every plan build; the
  capacity value itself remains node config (A-SRL-4), not a Postgres entity.

**Trade-off accepted:** a node whose real capacity differs from 40 Gbps and whose operator forgot
the env gets mis-sized **burst** admission (committed unaffected) until the env is set —
documented, observable via `congestion_drop` behavior.

---

## Assumptions carried from the spec (flagged, not user decisions)

A-FAIR-1..8 stand as drafted in `spec.md` — notably:

- **A-FAIR-1:** executes after **blacklist-filters** completes (last M3 feature; shared hot-path
  files; post-BLK baseline).
- **A-FAIR-2:** per-service rates ride a **new** slotted config map keyed by `service_id`
  (`service_val`'s pad bytes are exhausted); layout = Design output = M4 build contract; Gbps →
  bytes/sec at build time.
- **A-FAIR-5:** per-CPU splits (burst/node/cap) follow AD-019 rate÷nCPU — node aggregate never
  exceeds configured; committed needs no split (global + lock = exact).
- **A-FAIR-8:** `bpf_spin_lock` in XDP is the one novel kernel-semantics claim — verified at
  Design and de-risked fail-fast at the first load gate (FAIR-22) with a verdict-preserving
  fallback ladder.

## Agent discretion at Design

- Slotted rate-config map layout (one map for committed/burst/cap budgets vs split; key/value
  encoding) — becomes the M4 build contract (FAIR-20).
- Committed-bucket critical-section shape (lock scope, version check inside lock, refill math)
  and the spin-lock fallback ladder (FAIR-22/A-FAIR-8).
- Burst dual-draw ordering (service-then-node vs check-then-consume) and refund semantics
  (A-FAIR-6 — unobservable by any FAIR requirement).
- Reference packet size default + delivery (rodata vs env) for the derived pps cap (D-FAIR-1).
- Env names/defaults for capacity, k, ref-size; seed-helper extension shape (D-FAIR-2, FAIR-23).
- `pkt_meta` ladder-outcome field encoding (A-FAIR-7).
- Deterministic fairness-scenario mechanics (flood shape, token seeding, CPU pinning) for
  FAIR-24/25.

**Next phase:** Design.
