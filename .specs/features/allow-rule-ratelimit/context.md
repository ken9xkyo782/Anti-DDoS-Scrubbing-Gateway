# Allow-Rule Matching & Rate-Limit Context

**Gathered:** 2026-07-09
**Spec:** `.specs/features/allow-rule-ratelimit/spec.md` (approved as drafted, ARL-01..25)
**Status:** Ready for design

---

## Feature Boundary

Insert the §8.2 allow-rule / rate-limit stage between service-match and redirect: first-match by
ascending `priority` (terminal, no fall-through, AD-004), default-deny on no match (`not_allowed`),
per-rule aggregate per-CPU pps/bps token buckets (`rate_limit_drop`), slotted `rule_block_map` read via
the pinned `active_slot`, fail-closed map errors, frozen ABI indices 9/10 wired via `record_drop`, seed
helper as interim writer, 34-case suite migrated, marked admit→redirect seam for the M3 fairness ladder.
Out of scope: all other M3 stages (ingress cap, whitelist/VIP, blacklists, amplification/bogon, fairness
buckets), M4 worker build/swap, per-source-IP state, monitor-only mode (GA), per-rule telemetry (M5).

---

## Implementation Decisions

### `any` protocol scope — strict supported set (D-ARL-1)

- **`protocol = any` matches exactly {tcp, udp, icmp}** — the strict reading of PRD §6.4 ("ANY within
  the supported range").
- **Non-TCP/UDP/ICMP IPv4 protocols (GRE, ESP, …) are unmatchable by any rule → always `not_allowed`.**
  v1 cannot protect tunnel/IPsec traffic; this is a documented product statement (onboarding + README),
  consistent with the strongest default-deny posture. A-PKT-5's "continue down the pipeline" resolves to
  a deterministic `not_allowed` at this stage, never a pass.
- Sustained `not_allowed` from tunnel traffic is therefore *expected*, not an anomaly — worth a note in
  the drop-reason docs so operators don't chase it.
- Future path (captured as deferred idea): explicit protocol values (`gre`, `esp`, …) could be added
  later without changing `any`'s meaning.

### Bucket identity across config swaps — reset on swap (D-ARL-2)

- **Rate-limit buckets are keyed positionally/per-version (service, slot-position or equivalent), not by
  stable rule id — a config-slot flip resets the affected service's buckets.**
- Consequence (accepted): every apply of any rule/list edit on a service briefly re-grants full burst to
  all that service's rules — a flood matching a quota'd rule gets one extra burst per config apply
  (bounded by burst size, brief).
- Benefit: no rule-identity plumbing through the M4 build contract; smaller bucket keyspace; the map
  contract M4 must populate stays minimal (block = `rule_count` + ordered rule entries, nothing more).
- Exact key layout (per-service array vs per-(service,position) hash; where burst capacity lives) is a
  Design call within this decision.

### Agent's Discretion

Design-time calls (fail-fast verification per project convention, not assumption):

- **Per-CPU budget split** (A-ARL-5) — full-rate-per-CPU vs rate÷nCPU vs hybrid; the documented deviation
  bound ARL-14 requires. Precedent: AD-017's sampling bucket chose full-budget-per-CPU with the node
  bound documented.
- **Rule-block map layout** — inner map type for the slotted block (array-of-blocks vs hash by
  `service_id`), rule entry struct packing, where `rule_count` lives (ARL-18).
- **Deterministic test mode** — how `rate=0, burst=B` (AD-017 pattern) is expressed for rule buckets
  (ARL-17).
- **`pkt_meta` matched-rule field** — representation of matched-rule identity given D-ARL-2's positional
  keying (A-ARL-8).

---

## Specific References

- AD-004/BL-05 (PRD §6.4): first-match ascending priority, terminal, no fall-through — the semantic this
  feature implements; UI overlap warning already shipped (SRL-18).
- AD-005/§8.3: config slotted / runtime unslotted split — `rule_block_map` config, `rate_limit_state`
  runtime; D-ARL-2 defines how the two relate at swap time.
- AD-016/AD-017: indices 9/10 pre-reserved in the frozen ABI; `record_drop(meta, reason)` fuses exact
  count + sampling; `dpstat` decodes with zero changes.
- SRL `allow_rule` model (service-rule-list design): priority unique per service, protocol enum
  {tcp,udp,icmp,any}, nullable port ranges (NULL for icmp/any), nullable BigInteger pps/bps — the shape
  the in-kernel rule entry mirrors.
- D-SLRD-1: seed-helper-as-interim-writer posture reused for rule blocks (ARL-21).
- PRD §13 / TDD risk table: no per-source-IP hot-path state (spoofed-flood posture) — hard constraint on
  bucket design.

---

## Deferred Ideas

- [ ] Explicit tunnel protocol values (`gre`, `esp`, …) in the `AllowRule.protocol` enum so tunnel
      traffic becomes allowlistable without widening `any` (D-ARL-1 follow-on; GA candidate).
