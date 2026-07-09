# Whitelist/VIP (Scoped) & VIP Ceiling Specification

**Milestone:** M3 — Policy enforcement & fairness
**Feature #2 of M3** (the whitelist/VIP bypass + VIP-ceiling stage of the §8.2 pipeline)
**Category ID:** WLV
**Status:** Spec + context complete (approved 2026-07-09) → Design (1 gray area resolved: D-WLV-1)
**Discuss context:** `.specs/features/whitelist-vip/context.md` (D-WLV-1, A-WLV-1..8) —
GA-1 (NULL VIP ceiling) is **resolved**; see *Gray Areas* below.
**Depends on:**
- **Service lookup & transparent redirect** (`.specs/features/service-lookup-redirect/`) — consumes the
  pinned `active_slot`, the matched `service_id` in `pkt_meta`, the `ARRAY_OF_MAPS` double-buffer config
  pattern, and `redirect_out()`/`tx_devmap`. **Executed (VERIFIED).**
- **Drop-reason counters** (`.specs/features/drop-reason-counters/`) — consumes the frozen index ABI
  (`DR_VIP_CEILING_DROP = 14` already reserved, reading 0 until wired here) and the fused
  `record_drop(meta, reason)` count+sample helper. **Executed (VERIFIED).** No enum change, no
  renumbering — this feature makes exactly one existing index count.
- **Allow-rule matching & rate-limit** (`.specs/features/allow-rule-ratelimit/`) — this stage inserts
  **between the enabled-service hit and the rule stage** ARL wired in, on the same hot-path region; a
  whitelist **miss** continues into ARL's rule loop unchanged, a whitelist **hit** bypasses it. Also
  reuses ARL's lazy version-reset bucket pattern (D-ARL-2 / AD-019) and its deterministic-bucket test
  mode. **Execute in progress (T1 committed)** — this feature requires ARL executed first (shared files:
  `xdp_gateway.bpf.c`, `pkt_meta.h`, seed helper, dp-unit suite).
- **Service, rule & list management (API)** (`.specs/features/service-rule-list/`) — the source-of-truth
  `WhitelistEntry` rows (SRL-22..25): keyed (`service_id`, `source_cidr`), **arbitrary IPv4** source
  (external allowed, D-SRL-1), IPv6 rejected; and the service-level VIP ceiling `vip_pps`/`vip_bps`
  (nullable BigInteger, SRL-01/06/24). As with ARL, the dependency is **contractual, not a code
  import** — this feature does not read Postgres; the M4 worker will populate the maps from those rows.

**Decisions already made (bind this spec):**
- **AD-003 / BL-01 / BL-02 / PRD §6.5:** whitelist/VIP bypass is **scoped by `service_id`** — the
  data-plane key includes `service_id`, so an entry for service A can never bypass for service B; the
  bypass is evaluated per-packet and **never edits or removes** anything from the global
  blacklist/threat-feed maps.
- **PRD §6.5 / §8.2:** a whitelist hit bypasses bogon check, global/service blacklist, threat feed,
  UDP-amplification policy, and the allow-rule stage — but **only within the owning service's scope**,
  and it **remains subject to the VIP ceiling** (aggregate PPS/BPS per service); over ceiling →
  `vip_ceiling_drop`.
- **PRD §6.5:** whitelist/VIP requires the packet to match an **enabled** `ProtectedService` first —
  fail-fast drops (IPv6, malformed, fragment, unsupported EtherType) and `service_miss`/
  `service_disabled` all happen **before** whitelist is consulted; disable's drop-all overrides the
  whitelist (AD-002, SRL-42).
- **PRD §8.1 / §8.3:** whitelist lookup uses a **bloom filter before the LPM trie** (bloom = cheap
  negative guard, LPM = scoped confirm); both are **config maps (slotted, double-buffer)** read via the
  per-packet pinned `active_slot` (AD-005). `vip_ceiling_state` is **runtime state (unslotted)**.
- **PRD §8.4.6:** the whitelist/VIP branch uses its **own VIP ceiling** and does **not** go through the
  committed/burst/node fairness admit ladder (M3 #4) — VIP admit goes straight to redirect.
- **PRD §13 / BL-08:** no per-source-IP state on the hot path; the VIP ceiling is **aggregate** per
  service precisely so spoofed "whitelisted" floods exhaust a bounded budget (the residual self-DoS
  failure mode is documented, not hidden).

## Problem Statement

Once ARL lands, enabled services are default-deny: only traffic matching an allow-rule passes, and M3 #3
will add blacklists and amplification filters that drop even more. That leaves no way to guarantee that
**critical, known-good sources** (monitoring probes, partner APIs, the tenant's own offices) are never
caught by a feed entry, a bogon edge case, or a mis-ordered rule. PRD §6.5's whitelist/VIP gives those
sources a scoped bypass — keyed by `service_id` so one tenant's trust never weakens another's protection
— capped by an aggregate VIP ceiling so a spoofed "whitelisted" source cannot become an unlimited hole.
This feature inserts that stage between service-match and the rule loop: bloom-guarded scoped LPM match,
VIP-ceiling token bucket, `vip_ceiling_drop`.

## Goals

- [ ] A source covered by a whitelist entry of the destination's **enabled** service bypasses the rule
      stage (and, by pipeline position, every M3 #3 filter that will insert after whitelist-miss) and is
      redirected `IN→OUT` — provided the service's VIP ceiling has tokens.
- [ ] The whitelist key includes `service_id`: entry for service A never matches traffic to service B
      (BL-02); the global blacklist maps are never modified by whitelist processing (BL-01).
- [ ] Whitelisted traffic draws from an **aggregate per-service VIP token bucket** (`vip_pps`/`vip_bps`);
      over ceiling → `XDP_DROP` with reason `vip_ceiling_drop` at frozen ABI index 14 — terminal, no
      fall-through into the rule stage.
- [ ] Bloom filter guards the LPM lookup: a bloom miss is a definitive negative (no LPM cost on the
      common non-whitelisted path); a bloom hit is confirmed by the scoped LPM; false positives cost
      only a lookup, never change a verdict.
- [ ] Whitelist maps are slotted config (double-buffer, pinned slot, AD-005) whose layout is the M4
      build contract; `vip_ceiling_state` is unslotted runtime; the loader/seed helper can populate
      whitelist + ceiling so the stage is loadable/testable/demoable before M4 (D-SLRD-1 posture).
- [ ] The existing dp-unit suite still passes unchanged on the whitelist-miss path (no whitelist seeded
      = prior behavior); new WLV cases cover hit/scope/ceiling; the program still loads native/DRV
      fail-loud.

## Out of Scope

Explicitly excluded — owned by other features. Documented to prevent scope creep.

| Feature | Reason |
| --- | --- |
| Global/service **blacklist** (bloom→LPM), `blacklist_drop`, `bloom_hit_lpm_miss` counter, hardcoded UDP amplification ports, bogon check, dynamic blocked-port bitmap | M3 feature #3. In §8.2 those stages all sit **after whitelist-miss and before the rule loop**; they insert at this feature's marked miss-path seam. The bloom-FP observability counter is theirs (feed-driven sets are where fill-rate matters). |
| Ingress-cost cap (`ingress_cap_drop`) and the fairness ladder (committed/burst/node, `service_ceiling_drop`/`congestion_drop`) | M3 feature #4. The ingress cap will insert **before** the whitelist stage (§8.2 order); the ladder applies only to the non-VIP branch (§8.4.6) and never touches this feature's VIP path. |
| The M4 worker that builds whitelist maps + VIP ceiling config from Postgres, verifies, and flips `active_slot` | M4. This feature owns the read side + extends the SLRD/ARL seed helper (interim writer, D-SLRD-1 posture). |
| `WhitelistEntry` CRUD, IPv6 rejection, scoping/ownership checks, VIP-ceiling field CRUD, audit | Done — service-rule-list (SRL-22..25, control-plane). |
| "Whitelist IP overlaps threat feed → alert + audit" + admin policy flag forbidding it | M4 *Threat intelligence feed sync* (D-SRL-4/AD-003) — no feed exists yet. |
| `expires_at` reconciliation sweep for whitelist entries | GA (BL-07, deferred). v1 contract: the map contains only entries the builder considers active (A-WLV-4). |
| "VIP ceiling hit repeatedly → alert" (spoof signal, BL-08) | M6 *Alerting*. This feature provides the exact counter (index 14) that alert will consume. |
| Per-source or per-entry VIP quotas | Explicitly forbidden posture (PRD §13, no per-source hot-path state); the ceiling is aggregate per service. |
| Whitelist telemetry/dashboards (VIP traffic breakdown) | M5. This feature only bumps `counter_map` + samples drop events. |

---

## User Stories

### P1: Scoped whitelist match (bloom → LPM, no cross-service bypass) ⭐ MVP

**User Story**: As a service owner, I want sources I trust to be whitelisted **for my service only**, so
that their traffic is never dropped by rules or (future) blacklists on my service — while my trust never
weakens the protection of any other service or tenant.

**Why P1**: This is the tenant-isolation-preserving bypass AD-003 froze (BL-01/BL-02 were Pilot-blocking
findings); M3 #3's blacklists and M4's threat feed assume the scoped-bypass stage already sits in front
of them.

**Acceptance Criteria**:

1. WHEN a packet has matched an **enabled** service THEN the whitelist stage SHALL run **after the
   service verdict and before the rule stage**, reading whitelist config from the slot **pinned at
   ingress** (same slot as the service lookup; never re-read `active_slot` mid-packet). `(WLV-01)`
2. WHEN the whitelist is consulted THEN the source SHALL match iff a whitelist entry of **that**
   `service_id` covers the packet's source IPv4 (LPM/CIDR containment) — the lookup key SHALL include
   `service_id` (BL-02). `(WLV-02)`
3. WHEN a source is whitelisted for service A THEN traffic from that source to service B SHALL receive
   **no bypass** — it proceeds through B's normal pipeline exactly as if the entry did not exist
   (no cross-service bypass; independent of tenant). `(WLV-03)`
4. WHEN the whitelist is consulted THEN a **bloom-filter guard** SHALL run before the LPM lookup: a
   bloom miss SHALL be a definitive negative (LPM skipped — a source never inserted can never match); a
   bloom hit SHALL be confirmed by the scoped LPM before any bypass; a bloom **false positive** SHALL
   only cost the extra lookup, never change a verdict; false **negatives** SHALL be impossible for
   entries present in the same slot (build populates bloom ⊇ LPM). `(WLV-04)`
5. WHEN the source matches the service's whitelist AND the VIP ceiling admits (see next story) THEN the
   packet SHALL bypass the rule stage (and every future stage at the miss-path seam) and reach
   `redirect_out()` byte-for-byte unchanged. `(WLV-05)`
6. WHEN the source does **not** match (bloom miss, or bloom hit + LPM miss, or the service simply has
   no whitelist entries in the pinned slot) THEN the packet SHALL continue into the rule stage
   completely unchanged — a clean miss is never a drop, never an error, and leaves no side effects.
   `(WLV-06)`
7. WHEN a whitelist-stage map read fails structurally (slot inner missing, unexpected lookup error on a
   map that must exist) THEN the system SHALL fail closed with `DR_MAP_ERROR` — a broken config slot is
   never treated as "no whitelist" (distinct from the clean miss of WLV-06, same posture as ARL-19).
   `(WLV-07)`
8. WHEN whitelist processing runs THEN it SHALL be **read-only** with respect to all config maps — in
   particular the global blacklist maps (M3 #3) are never modified by any whitelist evaluation (BL-01);
   bypass exists only as a per-packet, per-scope verdict. `(WLV-08)`
9. WHEN the whitelist stage decides (hit, miss, or ceiling drop) THEN the outcome SHALL be recorded in
   `pkt_meta` (observable via `test_meta_map` under `-DPKT_TEST_HOOKS`) for tests and downstream stages.
   `(WLV-09)`

**Independent Test**: Seed service A (id 1) with whitelist `198.51.100.0/24` and service B (id 2) with
none; `BPF_PROG_TEST_RUN`: packet src `198.51.100.7` → A = bypass + redirect with no rule block seeded
(would be `not_allowed` otherwise); same src → B = `not_allowed` (no cross-service bypass); src
`203.0.113.9` → A = falls through to A's rules; A with zero whitelist entries behaves exactly as before
the feature.

---

### P1: VIP ceiling (aggregate per-service token bucket, `vip_ceiling_drop`) ⭐ MVP

**User Story**: As the platform operator, I want all whitelisted traffic of a service capped by an
aggregate PPS/BPS ceiling, so that an attacker spoofing a whitelisted source can exhaust at most a
bounded budget — never turn a whitelist entry into an unlimited bypass of every defence.

**Why P1**: The VIP ceiling is the **mandatory** mitigation for whitelist spoofing (PRD §6.5 risk
posture, BL-08); shipping scoped bypass without it would create exactly the unlimited hole the PRD
forbids.

**Acceptance Criteria**:

1. WHEN a whitelisted packet is admitted THEN it SHALL have drawn tokens from the service's **aggregate
   VIP ceiling bucket** — admission requires **every configured dimension** (`vip_pps` in packets,
   `vip_bps` in bytes) to have sufficient tokens. `(WLV-10)`
2. WHEN the VIP ceiling is exhausted on any configured dimension THEN the packet SHALL be dropped
   `XDP_DROP` with reason `vip_ceiling_drop` — **terminal**: an over-ceiling whitelisted packet never
   falls through into the rule stage for a second chance (PRD §8.2). `(WLV-11)`
3. WHEN the bucket is maintained THEN it SHALL be **aggregate per service** (all whitelisted sources of
   the service share one budget) with **per-CPU** state and zero per-source/per-entry state on the hot
   path (PRD §13, BL-08 spoofing posture). `(WLV-12)`
4. WHEN a service's `vip_pps` AND `vip_bps` are **both NULL** THEN its whitelist SHALL be **inactive** —
   entries grant no bypass and traffic behaves exactly as a clean miss (WLV-06); WHEN exactly one
   dimension is set THEN it governs and the NULL dimension is unlimited; WHEN a dimension is `0` THEN
   whitelisted traffic always drops `vip_ceiling_drop` on that dimension (explicit block, A-ARL-3
   consistent) (**D-WLV-1**). `(WLV-13)`
5. WHEN a packet is dropped by one dimension THEN it SHALL NOT consume quota from the other dimension
   (mirrors ARL-12 — a pps-exhausted spoof flood must not silently drain the bps budget). `(WLV-14)`
6. WHEN VIP ceiling values are delivered THEN they SHALL travel through **slotted config** (they are
   per-service config, swapped atomically with everything else), while bucket state lives in the
   **unslotted runtime map** `vip_ceiling_state`; a config swap of the service SHALL reset the service's
   VIP bucket (lazy version-reset, D-ARL-2/AD-019 precedent — one extra burst per apply, accepted).
   `(WLV-15)`
7. WHEN traffic spreads across CPUs/RSS queues THEN the node-level admitted VIP rate SHALL converge on
   the configured aggregate within a **documented deviation bound**; the per-CPU split strategy follows
   the AD-019 precedent (rate ÷ nCPU — node admit never exceeds the configured ceiling) unless Design
   documents a reason to deviate. `(WLV-16)`
8. WHEN tests need determinism THEN the VIP bucket SHALL support the established deterministic mode
   (no-refill + exact burst, the `rl_config.test_no_refill` / AD-017 pattern), so dp-unit cases assert
   exact admit/drop counts without wall-clock dependence. `(WLV-17)`
9. WHEN VIP-admitted packets proceed THEN they SHALL go **directly to redirect** — the VIP branch does
   not pass through the (future) committed/burst/node fairness ladder and never consumes those buckets
   (PRD §8.4.6); headers remain untouched (TTL/checksum semantics inherited from SLRD, not modified).
   `(WLV-18)`

**Independent Test**: Seed whitelist + deterministic VIP bucket `burst=3` on service A; 5 identical
whitelisted packets → 3 `XDP_REDIRECT` + 2 drops with counter index 14 reading exactly 2; none of the 2
overflow packets reaches the rule stage (a seeded match-all rule receives nothing); a service with the
GA-1 "unlimited" configuration admits N ≫ burst whitelisted packets.

---

### P1: Config maps, frozen-ABI wiring, seed & suite migration ⭐ MVP

**User Story**: As the gateway, I want the whitelist maps delivered through the same slotted config-map
contract as `service_map`/`rule_block_map`, so the M4 worker can build and atomically swap the whitelist
with everything else, and operators see `vip_ceiling_drop` in the existing observability with zero
tooling changes.

**Why P1**: AD-005's atomicity only holds if *all* config maps flip on one `active_slot` write; and the
BL-08 alert (M6) plus dashboards (M5) depend on index 14 counting exactly.

**Acceptance Criteria**:

1. WHEN the whitelist maps are defined THEN `whitelist_bloom` and `whitelist_lpm` SHALL be **slotted
   config maps** (double-buffer, selected by the pinned `active_slot`, same mechanism as
   `service_map`/`rule_block_map`), with the LPM keyed by `service_id` + source CIDR; the concrete key
   encoding, bloom granularity scheme, and per-service vs. shared-map layout are Design decisions whose
   result **is the M4 build contract** (A-WLV-1..2). `(WLV-19)`
2. WHEN `vip_ceiling_drop` verdicts are produced THEN they SHALL be recorded via `record_drop()` at the
   **frozen ABI index 14** — exact per-CPU counts plus rate-limited ringbuf sampling, no enum change, no
   renumbering; `dpstat` decodes the reason with **zero code changes**. `(WLV-20)`
3. WHEN the gateway loads THEN the loader/seed helper (SLRD's, ARL-extended) SHALL be able to populate
   whitelist entries and VIP ceiling values alongside services and rule blocks, so the stage is
   independently loadable, testable, and demoable before the M4 worker exists (D-SLRD-1 interim-writer
   posture). `(WLV-21)`
4. WHEN the stage is inserted THEN the existing dp-unit suite (the post-ARL baseline) SHALL pass with
   **no expectation changes**: no case seeds a whitelist, so every existing packet takes the miss path
   (WLV-06) and prior verdicts are preserved; new WLV cases are additive. `(WLV-22)`
5. WHEN the program is built THEN it SHALL still load in **native/DRV mode fail-loud**; bloom-map
   feasibility (map type availability on the target kernel, slot/map-in-map composition) SHALL be proven
   at the first build/load gate with a documented fallback that preserves the external contract
   (fail-fast de-risk, same posture as SLRD's map-in-map and ARL's verifier de-risk). `(WLV-23)`
6. WHEN the stage is wired THEN two **marked seams** SHALL exist: (a) on the whitelist-**miss** path
   between this stage and the rule stage, where M3 #3's amplification/bogon/blacklist filters insert;
   (b) **before** the whitelist stage on the enabled-service path, where M3 #4's ingress-cost cap
   inserts (§8.2 order: ingress-cap → whitelist → …). `(WLV-24)`
7. WHEN the feature lands THEN `TESTING.md`'s data-plane section SHALL document the whitelist-seeding
   and VIP-bucket determinism conventions, and `README`/docs note the BL-08 residual failure mode
   (spoofed VIP flood → legitimate VIP traffic sees `vip_ceiling_drop` — self-DoS bounded by design,
   alert = M6). `(WLV-25)`

**Independent Test**: `make bpf skel loader dpstat` builds; loader attaches natively with seeded
service + whitelist + ceiling; flipping the seeded slot's whitelist inner changes bypass verdicts only
via one `active_slot` write; `make test` — full post-ARL baseline unchanged + new WLV cases pass;
`dpstat counters` shows the `vip_ceiling_drop` row counting after over-ceiling test traffic.

---

## Edge Cases

- WHEN a whitelisted source sends to a **disabled** service THEN the packet drops `service_disabled`
  before whitelist is consulted — disable's drop-all overrides bypass (AD-002, SRL-42); entries are
  config-retained for re-enable.
- WHEN a whitelisted source sends to an **undeclared** destination THEN `service_miss` — whitelist
  requires a service match first (PRD §6.5).
- WHEN a whitelisted source emits IPv6 / malformed IPv4 / fragments / unsupported EtherType THEN the
  fail-fast stage drops it before whitelist is ever consulted (PRD §6.5) — whitelist never resurrects
  invalid packets.
- WHEN overlapping whitelist entries of the same service cover one source (e.g., `/24` and `/32`) THEN
  coverage by **any** entry is a hit — LPM prefix length carries no policy meaning here (presence-only
  value); duplicates/overlaps are legal.
- WHEN a service has whitelist entries but the packet's source matches none THEN the miss path is taken
  with both bloom and LPM consulted at most once each — no retry, no partial state.
- WHEN a bloom false positive occurs THEN the LPM confirm misses and the packet takes the normal miss
  path — verdict identical, one extra lookup; the `bloom_hit_lpm_miss` observability counter is M3 #3's.
- WHEN the same source is in a service's whitelist **and** (come M3 #3) a blacklist THEN whitelist wins
  by pipeline order — evaluated first, blacklist never consulted on the hit path (SRL-41, documented
  precedence).
- WHEN a VIP burst arrives within one refill window on a single CPU THEN admits never exceed the
  bucket's burst capacity for that window (no unbounded burst on idle buckets — mirrors ARL edge case).
- WHEN `vip_pps`/`vip_bps` are configured but the service has zero whitelist entries THEN the ceiling is
  inert (no packet ever reaches the VIP bucket) — legal configuration, no error.
- WHEN ARP frames traverse the gateway THEN they redirect before service lookup and never touch the
  whitelist stage (unchanged from SLRD).
- WHEN the whitelist stage is compiled but the seed populated `service_map` without any whitelist maps
  content THEN every packet takes the clean miss path (WLV-06) — an empty whitelist is an empty set,
  never an error (contrast WLV-07's structural failure).

---

## Gray Areas (RESOLVED — see `context.md`, D-WLV-1)

**GA-1 → D-WLV-1: NULL VIP ceiling = whitelist inactive (fail-safe).** A service with both `vip_pps`
and `vip_bps` NULL grants no bypass — its whitelist behaves as empty until a ceiling is set (option b,
the strongest reading of BL-08's mandatory-ceiling posture; no uncapped bypass can ever exist). One set
dimension governs alone (the NULL one is unlimited); `0` = explicit block per dimension (A-ARL-3
consistent). Control-plane warning for the inert-whitelist UX captured as a deferred idea (SRL
follow-up, out of scope here).

Original options considered:

### GA-1: NULL VIP ceiling — unlimited bypass, or is a ceiling mandatory?

`vip_pps`/`vip_bps` are **nullable** on `ProtectedService` (SRL-01, shipped). PRD §6.5 says whitelisted
traffic "vẫn chịu VIP ceiling" and the risk register calls the aggregate ceiling **"bắt buộc"**
(mandatory, BL-08 mitigation) — but neither says what NULL means. The data-plane must pick a semantic:

- **(a) NULL = unlimited.** Consistent with ARL's quota convention (A-ARL-3: NULL = unlimited, 0 =
  block). Simple, no control-plane change — but a tenant who whitelists a source and never sets a
  ceiling gets an **uncapped bypass of every defence**, exactly the spoofing hole BL-08's "mandatory
  ceiling" language exists to prevent. The mitigation becomes opt-in.
- **(b) NULL = whitelist inactive for that service (fail-safe).** Entries exist but grant no bypass
  until the tenant sets a ceiling; the data-plane treats a NULL-ceiling service's whitelist as empty.
  Strongest reading of "bắt buộc"; surprising UX (a whitelist that silently does nothing) unless the
  control-plane/UI warns — and SRL shipped no such validation.
- **(c) NULL = derived default ceiling.** E.g., a node-level default or a fraction of the plan's
  `ceiling_clean_gbps` (bps only — no natural pps analog). No unlimited hole, whitelist works out of
  the box; but invents a formula the PRD never states and couples VIP behavior to plan fields.

Whichever resolves, `0` should stay coherent with A-ARL-3 (0 = explicit block: whitelisted traffic of
that dimension always drops `vip_ceiling_drop`) unless the user decides otherwise.

Affects: WLV-13, onboarding docs, whether SRL needs a follow-up validation/warning (out of this
feature's scope but flagged), and the BL-08 risk posture.

---

## Assumptions (flagged, not user-blocking)

- **A-WLV-1:** The bloom granularity scheme for CIDR entries (a bloom can only test exact keys, not
  prefix containment — e.g., insert per configured prefix-length, or bloom on a fixed prefix of
  source+`service_id`) is a Design decision; the spec only requires the guard property of WLV-04
  (false negatives impossible, false positives cost-only).
- **A-WLV-2:** Whether `whitelist_lpm` is one shared LPM keyed `{service_id, prefix}` or per-service
  inner maps, and how `vip_pps`/`vip_bps` reach the kernel (extended `service_map` value vs. a separate
  slotted map), are Design decisions; the resulting layout is the M4 build contract (same posture as
  ARL's `rules.h`).
- **A-WLV-3:** Kernel bloom-map availability (`BPF_MAP_TYPE_BLOOM_FILTER`, ≥5.16) and its composition
  with the slot mechanism are proven at the first load gate (WLV-23); the documented fallback preserves
  the bloom-guard contract or degrades to LPM-only **inside the same external contract** (verdicts
  identical — bloom is a cost optimization, WLV-04).
- **A-WLV-4:** The map contains only entries the builder considers active: `enabled=false` entries are
  omitted at build time (LPM sets need no in-kernel flag, unlike ARL's positional blocks); `expires_at`
  enforcement is the deferred BL-07 sweep — until then expiry takes effect at the next rebuild that
  observes it (M4 semantics, out of scope here). The seed helper writes only active entries.
- **A-WLV-5:** VIP bucket refill reuses the established lazy `bpf_ktime_get_ns` refill + lazy
  version-reset machinery (AD-019); refill granularity/remainder handling follows `rules.h` precedent.
- **A-WLV-6:** VIP-admitted bytes are clean redirected traffic and will count toward M5 billing/clean
  counters like any other redirect — no special-casing here (flagged for M5's attention, BL-09/AD-006).
- **A-WLV-7:** `pkt_meta` grows whitelist-outcome observability (hit flag / stage verdict) within the
  existing struct-growth conventions; `test_meta_map` exposes it (D-PKT-4 pattern). If ARL's final
  layout leaves no pad byte, the struct may grow per convention (size assert updated deliberately).
- **A-WLV-8:** This feature executes **after ARL completes** (shared hot-path files and the dp-unit
  baseline count); the spec deliberately avoids pinning test counts — gates reference "the post-ARL
  baseline".

---

## Requirement Traceability

| Requirement ID | Story | Refs | Phase | Status |
| --- | --- | --- | --- | --- |
| WLV-01 | P1: Scoped whitelist match | §8.2 order, AD-005 pin | Tasks | In Tasks |
| WLV-02 | P1: Scoped whitelist match | §6.5/§8.3, AD-003, BL-02 | Tasks | In Tasks |
| WLV-03 | P1: Scoped whitelist match | §6.5/§12.3, BL-02 | Tasks | In Tasks |
| WLV-04 | P1: Scoped whitelist match | §8.1 bloom→LPM | Tasks | In Tasks |
| WLV-05 | P1: Scoped whitelist match | §6.5 bypass set | Tasks | In Tasks |
| WLV-06 | P1: Scoped whitelist match | §8.2 miss path | Tasks | In Tasks |
| WLV-07 | P1: Scoped whitelist match | §11.3 fail-closed, ARL-19 | Tasks | In Tasks |
| WLV-08 | P1: Scoped whitelist match | §6.5, BL-01, AD-003 | Tasks | In Tasks |
| WLV-09 | P1: Scoped whitelist match | test hooks (D-PKT-4) | Tasks | In Tasks |
| WLV-10 | P1: VIP ceiling | §6.5 ceiling, BL-08 | Tasks | In Tasks |
| WLV-11 | P1: VIP ceiling | §8.2 `vip_ceiling_drop` | Tasks | In Tasks |
| WLV-12 | P1: VIP ceiling | §13/§8.3 aggregate per-CPU | Tasks | In Tasks |
| WLV-13 | P1: VIP ceiling | **D-WLV-1**, A-ARL-3 precedent | Tasks | In Tasks |
| WLV-14 | P1: VIP ceiling | ARL-12 precedent | Tasks | In Tasks |
| WLV-15 | P1: VIP ceiling | §8.3 unslotted, D-ARL-2 | Tasks | In Tasks |
| WLV-16 | P1: VIP ceiling | AD-019 rate÷nCPU | Tasks | In Tasks |
| WLV-17 | P1: VIP ceiling | AD-017/AD-019 determinism | Tasks | In Tasks |
| WLV-18 | P1: VIP ceiling | §8.4.6 no admit ladder | Tasks | In Tasks |
| WLV-19 | P1: Config maps & wiring | §8.3 slotted, AD-005 | Tasks | In Tasks |
| WLV-20 | P1: Config maps & wiring | AD-016/AD-017 ABI idx 14 | Tasks | In Tasks |
| WLV-21 | P1: Config maps & wiring | D-SLRD-1 seed posture | Tasks | In Tasks |
| WLV-22 | P1: Config maps & wiring | suite migration precedent | Tasks | In Tasks |
| WLV-23 | P1: Config maps & wiring | native mandate + de-risk | Tasks | In Tasks |
| WLV-24 | P1: Config maps & wiring | M3 #3/#4 seams | Tasks | In Tasks |
| WLV-25 | P1: Config maps & wiring | A-PKT-2 TESTING.md, BL-08 doc | Tasks | In Tasks |

**Coverage:** 25 total, 25 mapped to tasks (T1–T5, `tasks.md`), 0 unmapped.

---

## Success Criteria

- [ ] Whitelist `198.51.100.0/24` on service A, nothing on service B, under `BPF_PROG_TEST_RUN`:
      src `198.51.100.7`→A redirects with **zero rules seeded**; the same src→B drops `not_allowed`;
      src outside the CIDR →A takes the rule path — scope isolation demonstrated end-to-end.
- [ ] Deterministic VIP bucket `burst=3`: exactly 3 redirects then `vip_ceiling_drop` with counter
      index 14 exact; overflow never reaches a seeded match-all rule (terminal, no fall-through).
- [ ] Post-ARL dp-unit baseline passes with zero expectation changes; new WLV cases pass; `make bpf
      skel loader dpstat` builds; the program loads native/DRV fail-loud (bloom feasibility proven or
      fallback documented).
- [ ] `dpstat counters` shows a live `vip_ceiling_drop` row with no `dpstat` code change.
- [ ] D-WLV-1 (NULL ceiling = whitelist inactive) reflected in WLV-13's behavior + onboarding/docs
      language: "whitelist requires a VIP ceiling to take effect".
