# Blacklist (Bloom + LPM) & Deny Filters Specification

> **Notice (2026-07-23):** Service-scoped blacklist functionality (BLK-03, BLK-04 service part) and BL-02 service posture have been **superseded by feature `service-blacklist-removal` (B2)**. Blacklist is now a single global admin-owned scope.

**Milestone:** M3 — Policy enforcement & fairness
**Feature #3 of M3** (the deny-filter stage of the §8.2 pipeline: amplification ports, bogon check,
dynamic blocked-port bitmap, global + service blacklist)
**Category ID:** BLK
**Status:** Spec + context complete (approved 2026-07-09) → Design (2 gray areas resolved: D-BLK-1, D-BLK-2)
**Discuss context:** `.specs/features/blacklist-filters/context.md` (D-BLK-1..2, A-BLK-1..8; AD-022)
**Depends on:**
- **Whitelist/VIP (scoped) & VIP ceiling** (`.specs/features/whitelist-vip/`) — this entire feature
  inserts at **WLV-24 seam B** (the whitelist-**miss** path, inside/after `whitelist_stage`, before
  ARL's rule stage); a whitelist **hit** bypasses every filter here by pipeline position (PRD §6.5).
  Also reuses AD-021's bloom→LPM composition (composite scoped LPM key, /24 bloom buckets +
  broad-entry escape, bloom de-risk ladder) for the service blacklist. **Tasks APPROVED — this
  feature requires WLV executed first** (shared hot-path files, seam B host, post-WLV dp-unit
  baseline).
- **Drop-reason counters** (`.specs/features/drop-reason-counters/`) — consumes the frozen index ABI:
  `DR_BOGON_DROP = 4`, `DR_UDP_AMPLIFICATION_DROP = 7`, `DR_BLACKLIST_DROP = 8` are already reserved
  (reading 0 until wired here) and the fused `record_drop(meta, reason)` helper. **Executed
  (VERIFIED).** No enum change, no renumbering — this feature makes exactly three existing indices
  count. The `bloom_hit_lpm_miss` counter (explicitly out of DRC's scope) is **owned here**.
- **Service lookup & transparent redirect** (`.specs/features/service-lookup-redirect/`) — consumes
  the pinned `active_slot`, `pkt_meta.service_id`, the `ARRAY_OF_MAPS` double-buffer config pattern,
  and the D-SLRD-1 seed-helper interim-writer posture. **Executed (VERIFIED).**
- **Allow-rule matching & rate-limit** (`.specs/features/allow-rule-ratelimit/`) — the stage this
  feature's miss path hands off to, unchanged. **Executed (VERIFIED).**
- **Service, rule & list management (API)** (`.specs/features/service-rule-list/`) — the
  source-of-truth `BlacklistEntry` rows: service-scoped entries (SRL-26..27, arbitrary IPv4 source,
  D-SRL-1) and admin **manual** global entries (`scope=global`, `service_id=NULL`, `source=manual`,
  SRL-28..30, D-SRL-4). As with ARL/WLV the dependency is **contractual, not a code import** — this
  feature never reads Postgres; the M4 worker populates the maps from those rows. Note: **no
  control-plane model exists for the dynamic blocked-port bitmap** (PRD §7.1 has no such entity) —
  see GA-2.

**Decisions already made (bind this spec):**
- **PRD §6.6:** non-whitelisted traffic matching either blacklist scope drops with reason
  `blacklist_drop`; blacklist lookup **must** use a bloom filter before the LPM trie; bloom false
  positives are measured by a `bloom_hit_lpm_miss` counter.
- **PRD §8.2 (order within the whitelist-miss branch):** hardcoded UDP amplification ports → bogon
  check → dynamic blocked-port bitmap → global blacklist (bloom → LPM) → service blacklist (bloom →
  LPM) → rule stage. Hardcoded ports run first to fail-fast common reflection **after** the VIP
  exception; the dynamic bitmap applies only after service match (both are inherently post-service
  here — the whole stage is).
- **AD-003 / BL-01 / SRL-41:** whitelist wins by pipeline order — a whitelist hit is evaluated first
  and no filter in this feature is consulted; whitelist processing never edits the global blacklist
  maps, and this feature is equally **read-only** over all config maps.
- **AD-005 / §8.3:** `global_blacklist_bloom`, `global_blacklist_lpm`, `service_blacklist_bloom`,
  `service_blacklist_lpm`, and `udp_blocked_port_bitmap` are **slotted config maps** (double-buffer,
  read via the per-packet pinned `active_slot`); their layout is the M4 build contract.
- **AD-016/AD-017:** drop-reason indices are frozen; `counter_map` is a **pure drop-reason ABI** —
  `bloom_hit_lpm_miss` is not a drop reason and must live outside it.
- **PRD §2 scale envelope:** up to **1M global blacklist entries** — the global maps' sizing
  contract.
- **PRD §13 / §11.1:** no per-source-IP state on the hot path; LPM lookups must be bloom-guarded.

## Problem Statement

After ARL and WLV, enabled services are default-deny with a scoped VIP bypass — but every
non-whitelisted packet still pays the full rule-stage cost, and there is no way to drop *known-bad*
traffic (threat-feed CIDRs, tenant-blacklisted sources, spoofed bogon ranges, UDP reflection floods)
before it reaches the rule loop. PRD §6.6/§8.2 close that gap with a deny-filter stage on the
whitelist-miss path: cheap always-on amplification/bogon checks, then bloom-guarded global and
service blacklists sized for the 1M-entry threat feed M4 will deliver. This feature builds that
stage and the bloom false-positive observability (`bloom_hit_lpm_miss`) that keeps the guard honest.

## Goals

- [ ] A non-whitelisted source covered by a **global** blacklist entry is dropped `blacklist_drop`
      for every service; one covered by a **service** blacklist entry is dropped only for that
      service (scoped by `service_id`, no cross-service effect).
- [ ] Both blacklist lookups are **bloom-guarded**: a bloom miss skips the LPM entirely; false
      positives cost one lookup and never change a verdict; false negatives are impossible for
      entries present in the pinned slot; every bloom hit that the LPM does not confirm increments
      `bloom_hit_lpm_miss`, visible to operators.
- [ ] UDP packets from hardcoded amplification source ports and from the dynamic blocked-port bitmap
      drop `udp_amplification_drop`; bogon/private/reserved sources drop `bogon_drop` — all three
      run **after** the whitelist stage (VIP exception preserved) and **before** the blacklist
      lookups, wiring frozen ABI indices 7 and 4.
- [ ] All five config maps are slotted (double-buffer, pinned slot) and their layout is the M4 build
      contract; the global maps honor the 1M-entry scale envelope; the seed helper can populate all
      of them so the stage is loadable/testable/demoable before M4 (D-SLRD-1 posture).
- [ ] The post-WLV dp-unit baseline still passes (with a deliberate, documented migration of any
      case whose source address falls in the bogon set); the program still loads native/DRV
      fail-loud.

## Out of Scope

Explicitly excluded — owned by other features. Documented to prevent scope creep.

| Feature | Reason |
| --- | --- |
| Threat-feed fetch/validate/dedup and feed-driven population of the global blacklist | M4 *Threat intelligence feed sync* (D-SRL-4). This feature only guarantees the map contract can hold 1M entries. |
| The M4 worker that builds blacklist maps from Postgres `BlacklistEntry` rows, verifies, and flips `active_slot` | M4. This feature owns the read side + extends the seed helper (interim writer). |
| `BlacklistEntry` CRUD (service + global manual), scoping/ownership, audit | Done — service-rule-list (SRL-26..30, control-plane). |
| Control-plane model/CRUD for the dynamic blocked-port bitmap | **Does not exist** (PRD §7.1 has no entity) — GA-2 resolves the v1 owner; any CRUD is a follow-up feature, not this one. |
| Ingress-cost cap (`ingress_cap_drop`) and the fairness admit ladder | M3 #4. The cap inserts at WLV seam A (before whitelist), upstream of this stage; the ladder is downstream of the rule stage. |
| `expires_at` reconciliation sweep for blacklist entries | GA (BL-07, deferred). v1 contract: the map contains only entries the builder considers active (A-WLV-4 posture). |
| "Whitelist IP overlaps threat feed → alert + audit" + admin policy flag | M4 feed sync (AD-003/D-SRL-4). |
| Bloom fill-rate/false-positive **alerting** thresholds | M6 *Alerting*. This feature provides the exact counter it will consume. |
| Blacklist/bloom telemetry dashboards | M5. This feature only bumps counters. |
| Monitor/count-only blacklist mode | GA (OP-04, deferred idea). |
| TCP SYN-flood / port-scan specific detection | GA (BL-04). The port filters here are UDP-source-port reflection controls only. |

---

## User Stories

### P1: Global & service blacklist (bloom → LPM, `blacklist_drop`) ⭐ MVP

**User Story**: As a platform operator (and as a service owner for my own scope), I want known-bad
sources dropped before they consume rule-stage budget — globally when the platform blacklists them,
per-service when a tenant does — so that threat intelligence and tenant deny-lists are enforced
without weakening any other tenant's traffic.

**Why P1**: PRD §6.6 is the core of this feature; M4's threat feed is pointless without the
enforcement stage, and the 1M-entry envelope makes the bloom guard mandatory (§11.1), not an
optimization.

**Acceptance Criteria**:

1. WHEN a packet has taken the whitelist-**miss** path of an enabled service THEN the blacklist
   stage SHALL run at the WLV-24 seam-B position — after the amplification/bogon/bitmap filters
   (next story), before the rule stage — reading all config from the slot **pinned at ingress**;
   a whitelist **hit** SHALL never consult any map of this feature. `(BLK-01)`
2. WHEN the source IPv4 is covered by any **global** blacklist entry in the pinned slot THEN the
   packet SHALL be dropped `XDP_DROP` with reason `blacklist_drop` — for **every** service and
   tenant, with no per-service opt-out. `(BLK-02)`
3. WHEN the source is covered by a **service** blacklist entry THEN the packet SHALL drop
   `blacklist_drop` **only** when its destination matched that entry's `service_id` — the lookup
   key SHALL include `service_id` (same scoping-by-key-construction posture as WLV-02/BL-02);
   an entry for service A SHALL have zero effect on traffic to service B. `(BLK-03)`
4. WHEN both lookups run THEN the order SHALL be global first, then service (§8.2); either hit
   SHALL produce the identical terminal `blacklist_drop`; the stage SHALL be **read-only** over all
   config maps. `(BLK-04)`
5. WHEN either blacklist is consulted THEN a **bloom-filter guard** SHALL run before its LPM
   lookup: a bloom miss SHALL be a definitive negative (LPM skipped); a bloom hit SHALL be
   confirmed by the LPM before any drop; a bloom false positive SHALL only cost the extra lookup,
   never change a verdict; false **negatives** SHALL be impossible for entries present in the same
   slot (build populates bloom ⊇ LPM). `(BLK-05)`
6. WHEN the source matches neither blacklist (bloom miss, or bloom hit + LPM miss, or empty maps in
   the pinned slot) THEN the packet SHALL continue into the rule stage completely unchanged — a
   clean miss is never a drop, never an error, and leaves no side effects. `(BLK-06)`
7. WHEN a blacklist-stage map read fails structurally (slot inner missing, unexpected lookup error
   on a map that must exist) THEN the system SHALL fail closed with `DR_MAP_ERROR` — a broken
   config slot is never treated as "no blacklist" (distinct from BLK-06's clean miss; same posture
   as WLV-07/ARL-19). `(BLK-07)`
8. WHEN the global blacklist maps are defined THEN their sizing SHALL honor the **1M-entry scale
   envelope** (PRD §2): `max_entries`/bloom parameters chosen so 1M CIDRs load with the false-
   positive characteristics documented in Design; the memory footprint SHALL be documented.
   `(BLK-08)`
9. WHEN the stage decides (global hit, service hit, clean miss) THEN the outcome SHALL be recorded
   in `pkt_meta` (observable via `test_meta_map` under `-DPKT_TEST_HOOKS`). `(BLK-09)`

**Independent Test**: Seed global blacklist `185.0.0.0/8` and service blacklist `45.45.0.0/16` on
service A (id 1), nothing on service B (id 2); `BPF_PROG_TEST_RUN`: src `185.1.2.3` → A **and** → B
both drop `blacklist_drop` (counter index 8); src `45.45.6.7` → A drops, → B passes to B's rule
stage; src `9.9.9.9` → A reaches the rule stage unchanged; with zero blacklist seeded the post-WLV
baseline verdicts are byte-identical.

---

### P1: Amplification & bogon filters (`udp_amplification_drop`, `bogon_drop`) ⭐ MVP

**User Story**: As the platform operator, I want the classic spoofed/reflected flood classes — UDP
reflection source ports and bogon source addresses — dropped by cheap always-on checks before any
expensive lookup, so volumetric reflection attacks burn minimal CPU and never reach the blacklist or
rule machinery.

**Why P1**: PRD §4.1 commits to UDP reflection/amplification filtering; §8.2 places these checks
explicitly; they are the cheapest drops in the whole policy pipeline and protect the classification
CPU that fairness (M3 #4) assumes.

**Acceptance Criteria**:

1. WHEN a packet takes the whitelist-miss path THEN the three filters SHALL run **before** the
   blacklist lookups in §8.2 order: hardcoded amplification ports → bogon check → dynamic
   blocked-port bitmap. `(BLK-10)`
2. WHEN a **UDP** packet's **source port** is in the hardcoded amplification set THEN it SHALL drop
   `XDP_DROP` with reason `udp_amplification_drop` (frozen ABI index 7); the set is the **D-BLK-1
   full set** — 17, 19, 53, 111, 123, 137, 161, 389, 520, 1900, 5353, 11211 — compile-time
   constant (changing it is a rebuild, not config). `(BLK-11)`
3. WHEN the packet's **source address** falls in the bogon/private/reserved set THEN it SHALL drop
   `XDP_DROP` with reason `bogon_drop` (frozen ABI index 4) regardless of L4 protocol; the set is
   compile-time constant (no §8.3 map exists for it), documented in full (A-BLK-1). `(BLK-12)`
4. WHEN a **UDP** packet's source port is set in the **dynamic blocked-port bitmap** of the pinned
   slot THEN it SHALL drop `udp_amplification_drop` (same reason as the hardcoded set — §8.2 labels
   both identically); the bitmap SHALL be a **slotted config map** whose only v1 writer is the seed
   helper (**D-BLK-2**, D-SLRD-1 posture). `(BLK-13)`
5. WHEN a non-UDP packet arrives THEN neither port filter SHALL evaluate it — TCP traffic from
   port 53/123/etc. is never port-dropped; the bogon check still applies. `(BLK-14)`
6. WHEN a source is whitelisted for the destination service (and the whitelist is active,
   D-WLV-1) THEN all three filters SHALL be bypassed by pipeline position — the VIP exception of
   PRD §6.5 covers amplification ports, bogons, and both blacklists alike. `(BLK-15)`
7. WHEN the dynamic bitmap in the pinned slot is empty THEN no dynamic port blocking SHALL occur
   (clean state, zero drops from BLK-13); an unreadable/missing bitmap SHALL fail closed per
   BLK-07's posture. `(BLK-16)`

**Independent Test**: With no whitelist seeded: UDP src-port from the hardcoded set → drop with
counter index 7; same packet as TCP → passes the port filters; src `10.1.2.3` (RFC 1918) → drop
index 4; seed bitmap port 9999 → UDP src-port 9999 drops index 7, src-port 9998 passes; whitelist
the source on service A with an active ceiling → the identical amplification-port packet to A
redirects (VIP exception), to B still drops.

---

### P1: Bloom false-positive observability (`bloom_hit_lpm_miss`) ⭐ MVP

**User Story**: As the platform operator, I want an exact counter of bloom hits that the LPM did not
confirm, so I can see when a filter's false-positive rate degrades (fill-rate too high) and schedule
a rebuild/resize before the LPM cost silently eats the hot path.

**Why P1**: PRD §6.6 mandates the counter; §12.5 makes "bloom false-positive counters work and are
visible to admin" an acceptance criterion; the M6 alert and M5 dashboards consume it. It is the only
observability that keeps the §11.1 bloom-guard mandate honest at 1M feed entries.

**Acceptance Criteria**:

1. WHEN any bloom-guarded lookup of this stage hits the bloom but the LPM does not confirm THEN the
   system SHALL increment a `bloom_hit_lpm_miss` counter — exact per-CPU counting on the hot path
   (same accuracy posture as `counter_map`, never sampled). `(BLK-17)`
2. WHEN the counter is defined THEN it SHALL live **outside** `counter_map` (which stays a pure
   drop-reason ABI, AD-017); it SHALL cover **every bloom-guarded stage** — global blacklist,
   service blacklist, and the whitelist stage's bloom (WLV deferred its FP counting here); whether
   it is one aggregate or per-stage counters is a Design decision (A-BLK-3). `(BLK-18)`
3. WHEN an operator runs `dpstat` THEN the counter SHALL be visible (new row/section — unlike the
   drop reasons this is a new surface, so a `dpstat` change is expected and allowed). `(BLK-19)`
4. WHEN the counter increments THEN the packet's verdict SHALL be unaffected — a false positive
   takes the normal miss/continue path (BLK-06/WLV-06); the counter is observability only.
   `(BLK-20)`

**Independent Test**: Deterministically induce a false positive (seed a bloom-only key via the seed
helper or a test hook — mechanism per Design): packet passes to the rule stage (verdict identical to
a clean miss) while `bloom_hit_lpm_miss` reads exactly 1; `dpstat` shows the counter; a confirmed
blacklist hit does **not** increment it.

---

### P1: Config maps, frozen-ABI wiring, seed & suite migration ⭐ MVP

**User Story**: As the gateway, I want the five deny-filter maps delivered through the same slotted
config contract as every other config map, so the M4 worker can atomically swap blacklists with
everything else (one `active_slot` write, BL-06), and operators see the three new drop reasons in
existing tooling with zero changes.

**Why P1**: AD-005's atomicity only holds if *all* config maps flip together; M4's `LIST_UPDATE`/
`FEED_SYNC` jobs build against exactly this layout.

**Acceptance Criteria**:

1. WHEN the maps are defined THEN `global_blacklist_bloom`, `global_blacklist_lpm`,
   `service_blacklist_bloom`, `service_blacklist_lpm`, and `udp_blocked_port_bitmap` SHALL be
   slotted config maps (double-buffer, selected by the pinned `active_slot`); the concrete layouts
   (key encodings, bloom granularity, bitmap representation) are Design decisions whose result
   **is the M4 build contract** — the service-scoped pair SHALL follow AD-021's composite-key and
   bloom-bucket patterns unless Design documents a reason to deviate. `(BLK-21)`
2. WHEN `bogon_drop` / `udp_amplification_drop` / `blacklist_drop` verdicts are produced THEN they
   SHALL be recorded via `record_drop()` at frozen ABI indices **4 / 7 / 8** — exact per-CPU counts
   plus rate-limited ringbuf sampling; no enum change, no renumbering; `dpstat`'s drop-reason
   output decodes them with **zero code changes**. `(BLK-22)`
3. WHEN the gateway loads THEN the seed helper (SLRD's, ARL/WLV-extended) SHALL be able to populate
   global entries, service entries, and bitmap ports alongside everything it already seeds, so the
   stage is independently loadable, testable, and demoable before the M4 worker exists (D-SLRD-1
   posture); the **default** seed SHALL leave all deny-filter maps empty so the baseline and live
   smoke keep their current behavior. `(BLK-23)`
4. WHEN the stage is inserted THEN the post-WLV dp-unit suite SHALL pass with expectations
   unchanged **except** cases whose packet sources fall inside the bogon set (the suite uses
   RFC 5737 documentation ranges, which are bogons in production terms) — those SHALL be migrated
   **deliberately and documented** (source addresses moved to non-bogon space, or the equivalent
   Design-chosen mechanism), keeping every case's *intent* intact; new BLK cases are additive.
   `(BLK-24)`
5. WHEN the program is built THEN it SHALL still load in **native/DRV mode fail-loud**; bloom-map
   composition SHALL reuse WLV's proven mechanism and de-risk ladder (BTF-static inners →
   loader-created → LPM-only fallback preserving verdicts, WLV-23/A-WLV-3), re-proven at the first
   build/load gate for the new maps — including the 1M-sizing load (BLK-08) at least once in a
   gated/manual check. `(BLK-25)`
6. WHEN the feature lands THEN `TESTING.md`'s data-plane section SHALL document blacklist/bitmap
   seeding and FP-induction conventions, and `README`/docs SHALL list the full hardcoded
   amplification-port set and bogon set verbatim, with the operational guidance the collateral
   implies ("a service that legitimately receives UDP from these source ports — e.g. a resolver's
   upstream DNS/NTP responses — must whitelist those sources", per GA-1's resolution). `(BLK-26)`

**Independent Test**: `make bpf skel loader dpstat` builds; loader attaches natively with the
default (empty deny-filter) seed and the live smoke passes unchanged; seeding a global entry and
flipping `active_slot` changes the verdict for a matching source via that single write; `make test`
— post-WLV baseline (with the documented bogon-source migration) + new BLK cases pass; `dpstat
counters` shows live `bogon_drop`/`udp_amplification_drop`/`blacklist_drop` rows after test traffic.

---

## Edge Cases

- WHEN a source is in the global blacklist **and** whitelisted for service A (active ceiling) THEN
  traffic to A redirects (whitelist first, §6.5/SRL-41) while the same source to service B drops
  `blacklist_drop` — the global map itself is never edited (BL-01).
- WHEN a source is covered by both the global and a service blacklist THEN the global lookup hits
  first — same verdict, same reason, no double-count.
- WHEN overlapping blacklist entries cover one source (`/8` and `/32`) THEN coverage by **any**
  entry is a hit — LPM prefix length carries no policy meaning (presence-only), duplicates legal.
- WHEN a bogon source is also blacklisted THEN it drops `bogon_drop` — the earlier stage wins;
  reason attribution follows §8.2 order.
- WHEN a UDP source port is in both the hardcoded set and the dynamic bitmap THEN the hardcoded
  check drops it first — indistinguishable in counters (same reason), acceptable.
- WHEN a fragment, malformed packet, IPv6, or unsupported EtherType arrives THEN the fail-fast
  stage has already dropped it — no filter here ever sees it (PRD §6.5 precedence).
- WHEN the destination has no declared service or a disabled one THEN `service_miss`/
  `service_disabled` verdicts stand — this stage runs only on the enabled-service whitelist-miss
  path.
- WHEN ARP frames traverse the gateway THEN they redirect before service lookup and never touch
  this stage (unchanged from SLRD).
- WHEN a bloom false positive occurs on either blacklist THEN the LPM confirm misses, the packet
  continues (verdict identical), and `bloom_hit_lpm_miss` increments — the only observable effect.
- WHEN the blacklists are empty in the pinned slot THEN the stage costs at most the bloom checks
  and the packet continues — an empty blacklist is an empty set, never an error (contrast BLK-07).
- WHEN a `BlacklistEntry` is `enabled=false` or expired THEN the builder/seed omits it (A-WLV-4
  posture; `expires_at` enforcement = BL-07, deferred) — the map contains only active entries.
- WHEN 1M global entries are loaded THEN lookups stay bloom-guarded and the load itself succeeds
  (BLK-08/BLK-25); dp-unit never loads 1M entries (gated/manual check only, A-BLK-6).
- WHEN traffic arrives as TCP from source port 53 THEN it is never amplification-dropped (BLK-14) —
  only the bogon and blacklist checks apply to it.

---

## Gray Areas (RESOLVED — see `context.md`, D-BLK-1..2)

**GA-1 → D-BLK-1: full hardcoded set including 53/123** (option a): 17, 19, 53, 111, 123, 137,
161, 389, 520, 1900, 5353, 11211 — compile-time constants. Resolver/NTP tenants whitelist their
upstream sources (VIP exception, active ceiling required per D-WLV-1); onboarding guidance in
BLK-26. Novel ports belong to the dynamic bitmap, not hardcoded-set growth.

**GA-2 → D-BLK-2: seed-only bitmap writer in v1** (option a): enforcement + slotted map contract
ship now; the seed helper is the only writer (D-SLRD-1 posture); no control-plane model/CRUD/M4
job created here. Control-plane writer (admin CRUD or GA auto-response OP-02) captured as a
deferred idea in STATE.md.

Original options considered:

### GA-1: Hardcoded UDP amplification port set — content & collateral policy

PRD §8.2 mandates a **hardcoded** (compile-time) UDP source-port drop list but never enumerates it.
The classic reflection vectors are: 17 (QOTD), 19 (chargen), 53 (DNS), 111 (portmap), 123 (NTP),
137 (NetBIOS), 161 (SNMP), 389 (CLDAP), 520 (RIP), 1900 (SSDP), 5353 (mDNS), 11211 (memcached).
The decision is really about **collateral**: dropping UDP src-port 53/123 kills legitimate DNS/NTP
*responses* to any tenant service that makes upstream queries (e.g. a resolver or NTP server behind
the gateway) unless those upstreams are whitelisted (with an active VIP ceiling, D-WLV-1).

- **(a) Full set including 53/123.** Strongest reflection posture; matches the PRD's fail-fast
  intent. Tenants running resolvers/NTP behind the gateway must whitelist upstream servers —
  operational guidance documented (BLK-26). Most inbound-only services (web, game, API) never see
  legitimate UDP src 53/123 anyway.
- **(b) Conservative set excluding 53/123** (keep 17, 19, 111, 137, 161, 389, 520, 1900, 5353,
  11211). No resolver collateral out of the box; DNS/NTP reflection floods fall through to the
  dynamic bitmap (operator can block src 53 per-attack) and rate-limits instead. Weaker default
  against the two most common reflection vectors.
- **(c) Full set, with per-service opt-out flag.** Maximum flexibility but invents config the PRD
  calls "hardcoded", adds a per-service branch to the cheapest check, and needs control-plane
  schema — scope creep.

Affects: BLK-11, BLK-26 docs, onboarding language, and how much work the dynamic bitmap (GA-2) must
carry in practice.

### GA-2: Dynamic blocked-port bitmap — who writes it in v1?

§8.3 defines `udp_blocked_port_bitmap` as a slotted config map ("dynamic source-port block"), but
PRD §7.1 has **no data model** for it, SRL shipped no CRUD, and no M4 job is specced to build it.
Someone must own its contents:

- **(a) Ship enforcement + map contract now; seed helper is the only v1 writer.** Same D-SLRD-1
  posture as every other config map before its writer existed. The bitmap becomes operator-usable
  when a writer lands (M4 worker extension or the GA auto-response OP-02); until then it is
  demoable via seed and covered by tests. No control-plane work now.
- **(b) Add a minimal admin CRUD (control-plane follow-up feature) in M3/M4.** Makes the "dynamic"
  promise real for operators in the pilot (block a novel reflection port during an attack without a
  rebuild), but requires a new model + endpoints + M4 build path — a new control-plane feature this
  data-plane feature would depend on or trigger.

Affects: BLK-13, BLK-23, whether a follow-up control-plane feature enters the ROADMAP, and the
pilot's operational story for novel reflection ports.

---

## Assumptions (flagged, not user-blocking)

- **A-BLK-1:** The bogon set = the stable IANA special-purpose IPv4 ranges that must never appear
  as public source addresses: `0.0.0.0/8`, `10/8`, `100.64/10`, `127/8`, `169.254/16`,
  `172.16/12`, `192.0.0/24`, `192.0.2/24`, `192.168/16`, `198.18/15`, `198.51.100/24`,
  `203.0.113/24`, `224/4`, `240/4`, `255.255.255.255/32` — compile-time constants (no §8.3 map),
  final list confirmed at Design and documented verbatim (BLK-26). **Consequence:** the RFC 5737
  documentation ranges the dp-unit suite uses as sources become bogons — BLK-24 makes that suite
  migration explicit and deliberate (production correctness over test convenience).
- **A-BLK-2:** The service blacklist reuses AD-021's machinery patterns (composite scoped LPM key
  `{service_id, src}`, /24 bloom buckets + broad-entry escape, replace-only bloom inners); the
  global blacklist drops the `service_id` dimension (plain source-prefix LPM key, source-only bloom
  keys). Exact layouts = Design, and become the M4 build contract (BLK-21).
- **A-BLK-3:** `bloom_hit_lpm_miss` granularity (one aggregate vs. per-stage counters for
  whitelist/global/service) and its map home (a small per-CPU array beside `sample_stats`, or
  similar) are Design decisions; the spec requires only exactness, coverage of all bloom-guarded
  stages, and `dpstat` visibility (BLK-17..19).
- **A-BLK-4:** Blacklist entries need no activation gate analogous to D-WLV-1 — a blacklist entry
  is active as built (deny-side fail-safe is the opposite direction); `enabled=false`/expired rows
  are omitted at build time (A-WLV-4 posture).
- **A-BLK-5:** This feature executes **after WLV completes** (seam B lives inside WLV's stage;
  shared hot-path files; the baseline is post-WLV) — the spec avoids pinning test counts and
  references "the post-WLV baseline".
- **A-BLK-6:** The 1M-entry envelope is verified as a sizing/load contract (map parameters + a
  gated or manual bulk-load check), not as a dp-unit fixture; dp-unit uses small seeded sets.
- **A-BLK-7:** The dynamic bitmap is **node-global** (one bitmap, all services) — §8.3 lists a
  single map and no per-service dimension; a per-service bitmap would be a Design deviation
  requiring justification.
- **A-BLK-8:** `pkt_meta` grows a deny-stage outcome field within existing struct-growth
  conventions (D-PKT-4/A-WLV-7 pattern); `test_meta_map` exposes it.

---

## Requirement Traceability

| Requirement ID | Story | Refs | Phase | Status |
| --- | --- | --- | --- | --- |
| BLK-01 | P1: Global & service blacklist | §8.2 order, WLV-24 seam B | Tasks | In Tasks |
| BLK-02 | P1: Global & service blacklist | §6.6, SRL-28..30 | Tasks | In Tasks |
| BLK-03 | P1: Global & service blacklist | §6.6, SRL-26..27, BL-02 posture | Tasks | In Tasks |
| BLK-04 | P1: Global & service blacklist | §8.2, BL-01 | Tasks | In Tasks |
| BLK-05 | P1: Global & service blacklist | §6.6/§8.1/§11.1 bloom→LPM | Tasks | In Tasks |
| BLK-06 | P1: Global & service blacklist | §8.2 miss path | Tasks | In Tasks |
| BLK-07 | P1: Global & service blacklist | §11.3 fail-closed, WLV-07/ARL-19 | Tasks | In Tasks |
| BLK-08 | P1: Global & service blacklist | §2 scale envelope (1M) | Tasks | In Tasks |
| BLK-09 | P1: Global & service blacklist | test hooks (D-PKT-4) | Tasks | In Tasks |
| BLK-10 | P1: Amplification & bogon | §8.2 filter order | Tasks | In Tasks |
| BLK-11 | P1: Amplification & bogon | §8.2/§12.4, **D-BLK-1**, ABI idx 7 | Tasks | In Tasks |
| BLK-12 | P1: Amplification & bogon | §8.2/§10.2, A-BLK-1, ABI idx 4 | Tasks | In Tasks |
| BLK-13 | P1: Amplification & bogon | §8.2/§8.3/§12.4, **D-BLK-2** | Tasks | In Tasks |
| BLK-14 | P1: Amplification & bogon | §8.2 (UDP source port only) | Tasks | In Tasks |
| BLK-15 | P1: Amplification & bogon | §6.5 VIP exception | Tasks | In Tasks |
| BLK-16 | P1: Amplification & bogon | empty-config posture | Tasks | In Tasks |
| BLK-17 | P1: Bloom FP observability | §6.6 `bloom_hit_lpm_miss` | Tasks | In Tasks |
| BLK-18 | P1: Bloom FP observability | AD-017 pure ABI, WLV deferral | Tasks | In Tasks |
| BLK-19 | P1: Bloom FP observability | §12.5 admin visibility | Tasks | In Tasks |
| BLK-20 | P1: Bloom FP observability | §8.1 FP = cost-only | Tasks | In Tasks |
| BLK-21 | P1: Maps, ABI, seed & suite | §8.3 slotted, AD-005/AD-021 | Tasks | In Tasks |
| BLK-22 | P1: Maps, ABI, seed & suite | AD-016/17 idx 4/7/8 | Tasks | In Tasks |
| BLK-23 | P1: Maps, ABI, seed & suite | D-SLRD-1 seed posture | Tasks | In Tasks |
| BLK-24 | P1: Maps, ABI, seed & suite | suite migration, A-BLK-1 | Tasks | In Tasks |
| BLK-25 | P1: Maps, ABI, seed & suite | native mandate, WLV-23 ladder | Tasks | In Tasks |
| BLK-26 | P1: Maps, ABI, seed & suite | A-PKT-2 TESTING.md, GA-1 docs | Tasks | In Tasks |

**Coverage:** 26 total, 26 mapped to tasks (T1–T8, `tasks.md`), 0 unmapped.

---

## Success Criteria

- [ ] Global entry `185.0.0.0/8` + service-A entry `45.45.0.0/16` under `BPF_PROG_TEST_RUN`:
      global source drops for **both** services (index 8); service-scoped source drops only for A;
      an uncovered source reaches the rule stage — scoping demonstrated end-to-end.
- [ ] UDP from a hardcoded amplification port drops index 7 while the same source whitelisted (with
      active ceiling) redirects — the VIP exception is real; TCP from the same port is untouched.
- [ ] RFC 1918 source drops index 4; a seeded bitmap port drops index 7 and clears when the slot
      flips — one `active_slot` write changes deny behavior atomically.
- [ ] An induced bloom false positive leaves the verdict unchanged and reads exactly 1 on
      `bloom_hit_lpm_miss` via `dpstat`; a confirmed hit does not increment it.
- [ ] Post-WLV baseline passes with only the documented bogon-source migration; `make bpf skel
      loader dpstat` builds; native/DRV load stays fail-loud; the 1M-entry load succeeds in the
      gated check.
- [ ] GA-1/GA-2 decisions reflected in BLK-11/BLK-13 behavior and the BLK-26 docs language.
