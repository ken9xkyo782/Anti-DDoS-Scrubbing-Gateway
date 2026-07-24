# Allow-Rule Matching & Rate-Limit Specification

> **⚠️ Amended by `service-ratelimit` (SVR, 2026-07-24).** The **per-rule `pps`/`bps` rate-limit
> described below was removed.** It was never plumbed through the M4 apply wire format (rule records
> carry only ports/proto/flags), so setting the flags black-holed a rule's traffic as `rate_limit_drop`
> (diagnosed 2026-07-20). Allow rules are now **pure matchers** (protocol + port ranges + enabled +
> priority). Rate-limiting moved to a **single per-service aggregate** — `ProtectedService.service_pps` /
> `service_bps`, enforced by `svc_rl_admit()` at the same clean-path allow→admit seam, reusing
> `DR_RATE_LIMIT_DROP` (idx 10). The first-match-by-priority + terminal-verdict semantics in this spec
> still hold; only the *source* of the rate-limit changed from per-rule to per-service. See
> `.specs/features/service-ratelimit/`.

**Milestone:** M3 — Policy enforcement & fairness
**Feature #1 of M3** (the allow-rule / rate-limit stage of the §8.2 pipeline)
**Category ID:** ARL
**Status:** Spec + context complete (approved 2026-07-09) — awaiting approval → Design (2 gray areas resolved: D-ARL-1..2)
**Discuss context:** `.specs/features/allow-rule-ratelimit/context.md` (D-ARL-1..2, A-ARL-1..8) —
GA-1 (`any` scope) and GA-2 (bucket identity across swaps) are **resolved**; see *Gray Areas* below.
**Depends on:**
- **Service lookup & transparent redirect** (`.specs/features/service-lookup-redirect/`) — consumes the
  pinned `active_slot`, the matched `service_id` in `pkt_meta`, the `ARRAY_OF_MAPS` double-buffer config
  pattern, `tx_devmap`/`redirect_out()`, and the enabled-service hit path this stage inserts into.
  **Executed (VERIFIED).**
- **Drop-reason counters** (`.specs/features/drop-reason-counters/`) — consumes the frozen index ABI
  (`DR_NOT_ALLOWED = 9`, `DR_RATE_LIMIT_DROP = 10` already reserved, reading 0 until wired here) and the
  fused `record_drop(meta, reason)` count+sample helper. **Executed (VERIFIED).** No enum change, no
  renumbering — this feature only makes two existing indices count.
- **Service, rule & list management (API)** (`.specs/features/service-rule-list/`) — the source-of-truth
  `allow_rule` rows (SRL-15..21) whose shape this feature's in-kernel rule block mirrors: `priority`
  unique per service (ascending = first-match, AD-004), `protocol ∈ {tcp, udp, icmp, any}`, nullable
  src/dst port ranges (NULL for icmp/any), nullable `pps`/`bps` aggregate limits, `enabled`. As with
  SLRD, the dependency is **contractual, not a code import** — this feature does not read Postgres; the
  M4 worker will populate the map from those rows.

**Decisions already made (bind this spec):**
- **AD-004 / BL-05 / PRD §6.4:** first enabled rule matching by ascending `priority` decides the verdict
  and is **terminal**; matched-but-out-of-quota → `rate_limit_drop`, **no fall-through** to looser rules.
- **PRD §6.4 / §13:** no per-source-IP state on the hot path (anti hash-map-thrashing under spoofed
  floods); token buckets are **aggregate per rule**, per-CPU, with documented error by CPU/RSS
  distribution; the rule loop must use the real `rule_count` and early-exit — never a fixed 16 iterations.
- **AD-005 / §8.3:** the rule block is **config** (slotted, double-buffer, read via the per-packet pinned
  `active_slot`); `rate_limit_state` is **runtime state** (unslotted).

## Problem Statement

Since SLRD, every packet to an enabled service is redirected `IN→OUT` unconditionally — the gateway
forwards any protocol to any port at any rate as long as the destination is declared. That is not
protection; PRD §6.4's allowlist model says traffic must match an operator-defined **allow-rule**
(protocol + optional port ranges) to pass, and each rule carries an **aggregate PPS/BPS quota** so a
matched-but-flooding flow is shed with `rate_limit_drop`. This feature inserts that stage between
service-match and redirect: first-match by ascending priority, terminal verdict, per-rule per-CPU token
buckets — turning enabled services from pass-through into **default-deny allowlists**.

## Goals

- [ ] Every packet to an enabled service is resolved against the service's rule block (≤16 rules, read
      from the pinned slot): first enabled rule matching on protocol + port ranges, in ascending
      `priority` order, decides the verdict — no later rule is ever consulted (AD-004).
- [ ] No matching rule (including a service with zero rules) → `XDP_DROP` with reason `not_allowed` —
      enabled services become default-deny.
- [ ] A matched rule admits iff every **configured** quota dimension (`pps`, `bps`) has tokens; out of
      quota → `XDP_DROP` with reason `rate_limit_drop`, terminal, no fall-through.
- [ ] Buckets are aggregate per rule, per-CPU, refilled from wall-clock time — zero per-source-IP state;
      node-level deviation from the configured aggregate rate is bounded and documented.
- [ ] The rule loop iterates at most the block's actual `rule_count` (≤16) with early exit on the
      terminal verdict; the program still loads natively (verifier-safe bounded loop).
- [ ] `DR_NOT_ALLOWED` (9) and `DR_RATE_LIMIT_DROP` (10) count exactly in `counter_map` and sample
      through the existing ringbuf path via `record_drop` — the ABI's first two M3 reasons go live.
- [ ] Admitted packets reach `redirect_out()` unchanged (header preservation untouched), leaving a marked
      **admit→redirect seam** where the M3 fairness ladder (committed/burst/node, feature #4) will insert.

## Out of Scope

Explicitly excluded — owned by other features. Documented to prevent scope creep.

| Feature | Reason |
| --- | --- |
| Ingress-cost cap, whitelist/VIP + VIP ceiling, hardcoded/dynamic UDP amplification ports, bogon check, global/service blacklist (bloom→LPM) | M3 features #2/#3. In §8.2 they all sit **between service-match and the rule loop**; they will insert *before* this stage later. Until then, traffic flows service-match → rule stage directly. |
| Fairness ladder: committed/burst buckets (`service_agg_rate_state`), node headroom (`node_burst_state`), `service_ceiling_drop` / `congestion_drop` / `ingress_cap_drop` / `vip_ceiling_drop` | M3 feature #4. This feature's admit path goes straight to redirect; the ladder inserts at the marked admit→redirect seam. |
| The M4 worker that builds rule blocks from Postgres `allow_rule` rows, verifies, and flips `active_slot` | M4. This feature owns the read side + extends the SLRD-established seed helper so the stage is loadable/testable standalone (same posture as D-SLRD-1). |
| `AllowRule` CRUD, validation (≤16, unique priority, port-range checks), overlap warning | Done — service-rule-list (SRL-15..21, control-plane). |
| Per-source-IP rate limiting or connection state | Explicitly forbidden on the hot path (PRD §6.4/§13, spoofed-flood posture). |
| Monitor/count-only rule mode | GA (OP-04, deferred). v1 rules enforce. |
| Per-rule hit/drop telemetry aggregation, dashboards | M5. This feature only bumps `counter_map` + samples drop events. |
| ICMP type/code filtering inside rules | Not in the PRD rule model; rules match ICMP as a protocol only (A-ARL-1). |

---

## User Stories

### P1: First-match rule verdict (ascending priority, terminal, default-deny) ⭐ MVP

**User Story**: As a service owner, I want only traffic matching my ordered allow-rules to pass, so that
anything I haven't explicitly allowed — wrong protocol, wrong port, or no rule at all — is dropped with
an attributable reason.

**Why P1**: This is the allowlist core of PRD §6.4 and the semantic AD-004 froze; every other M3 stage
assumes the rule loop exists as the pipeline's default-deny gate.

**Acceptance Criteria**:

1. WHEN a packet reaches the rule stage (destination matched an **enabled** service) THEN the system
   SHALL read that service's rule block from the config slot **pinned at ingress** (same slot as the
   service lookup; never re-read `active_slot` mid-packet). `(ARL-01)`
2. WHEN the rule block is evaluated THEN the system SHALL test rules in **ascending `priority` order**
   and the **first `enabled` rule that matches** SHALL decide the verdict — later rules SHALL NOT be
   consulted regardless of their quota state (AD-004, terminal first-match). `(ARL-02)`
3. WHEN a rule's `protocol` is `tcp`, `udp`, or `icmp` THEN it SHALL match only packets whose IPv4
   protocol equals it; `any` SHALL match exactly {tcp, udp, icmp} — other IPv4 protocols are unmatchable
   by any rule and always drop `not_allowed` (**D-ARL-1**). `(ARL-03)`
4. WHEN a rule carries a src and/or dst port range THEN it SHALL match only TCP/UDP packets whose
   corresponding port lies **inclusively** within each configured range; an absent (NULL) range is a
   wildcard for that dimension. `(ARL-04)`
5. WHEN a rule is `disabled` THEN the system SHALL skip it entirely — it neither matches nor terminates
   the scan. `(ARL-05)`
6. WHEN **no** enabled rule matches — including a service whose rule block is empty or absent — THEN the
   system SHALL return `XDP_DROP` with reason `not_allowed` (default-deny; an absent block is an empty
   block, never an error or a pass). `(ARL-06)`
7. WHEN the rule block holds `rule_count` < 16 rules THEN the loop SHALL iterate at most `rule_count`
   entries and SHALL early-exit on the terminal verdict; `rule_count` read from the map SHALL be clamped
   to the 16-rule cap (a corrupt count never over-reads). `(ARL-07)`
8. WHEN a rule decides the verdict THEN the system SHALL record the matched rule's identity in `pkt_meta`
   (observable via `test_meta_map` under `-DPKT_TEST_HOOKS`) for tests and downstream stages. `(ARL-08)`

**Independent Test**: `BPF_PROG_TEST_RUN` with a seeded service + rule block — UDP packet vs
`[p10: tcp/80, p20: udp dst 53]` → matches p20 (first-match order respected); TCP:443 vs the same block →
`not_allowed`; disabled p10 + TCP:80 → `not_allowed`; service with zero rules → all traffic `not_allowed`;
dst-port 79/80/81 probes an `80–80` range boundary.

---

### P1: Per-rule aggregate rate-limit (token buckets, terminal `rate_limit_drop`) ⭐ MVP

**User Story**: As a service owner, I want each allow-rule to enforce an aggregate PPS/BPS quota, so that
a flood matching a legitimate rule is shed at the rule's configured rate instead of saturating my service.

**Why P1**: The rate-limit half of PRD §6.4; the volumetric-flood defence for allowed traffic classes
(UDP/SYN/ICMP flood on allowed ports). AD-004's no-fall-through exists precisely so this quota is
meaningful.

**Acceptance Criteria**:

1. WHEN the first matching enabled rule has a configured `pps` and/or `bps` quota THEN the system SHALL
   admit the packet **iff every configured dimension** has sufficient tokens (packet count for `pps`,
   packet bytes for `bps`). `(ARL-09)`
2. WHEN the matched rule is out of quota on any configured dimension THEN the system SHALL return
   `XDP_DROP` with reason `rate_limit_drop` — terminal, **never** falling through to a later rule
   (AD-004/BL-05). `(ARL-10)`
3. WHEN a quota dimension is unset (NULL in the control-plane row) THEN that dimension SHALL be
   unlimited; a rule with both dimensions unset SHALL always admit on match. `(ARL-11)`
4. WHEN a packet is dropped by one dimension THEN it SHALL NOT consume quota from the other dimension
   (a `pps`-exhausted flood must not silently drain the `bps` budget, and vice versa). `(ARL-12)`
5. WHEN buckets are maintained THEN they SHALL be **aggregate per rule** with **per-CPU** state refilled
   from elapsed time — no per-source-IP or per-flow state on the hot path (PRD §6.4/§13). `(ARL-13)`
6. WHEN traffic is spread across CPUs/RSS queues THEN the node-level admitted rate SHALL converge on the
   configured aggregate within a **documented deviation bound** (per-CPU split strategy and its error
   model are a Design decision, A-ARL-5); exactness per-CPU is not required, but the bound is. `(ARL-14)`
7. WHEN a configured quota is `0` THEN matched traffic SHALL always drop with `rate_limit_drop`
   (0 = explicit block quota, distinct from NULL = unlimited, A-ARL-3). `(ARL-15)`
8. WHEN bucket state is stored THEN it SHALL live in an **unslotted runtime map** (`rate_limit_state`,
   §8.3) — a config-slot flip does not flip bucket storage; buckets are keyed positionally/per-version,
   so a config swap **resets the affected service's buckets** (one extra burst per apply, accepted;
   **D-ARL-2**). `(ARL-16)`
9. WHEN tests need determinism THEN bucket refill SHALL be configurable to a deterministic mode (the
   `rate=0, burst=B` pattern established by drop-reason counters' `sample_bucket`), so dp-unit cases can
   assert exact admit/drop counts without wall-clock dependence. `(ARL-17)`

**Independent Test**: Seed a rule with `pps` unset/`bps` unset → N packets all admitted; seed
deterministic quota `burst=3` → 5 identical packets yield 3 `XDP_REDIRECT` + 2 `rate_limit_drop` and
counter index 10 reads exactly 2; a second looser rule at higher priority receives none of the overflow
(no fall-through); `pps=0` → all matched packets drop.

---

### P1: Rule-block config map, fail-closed reads & suite migration ⭐ MVP

**User Story**: As the gateway, I want the rule block delivered through the same slotted config-map
contract as `service_map`, so the M4 worker can build and atomically swap rules with everything else,
and map failures never become an implicit allow.

**Why P1**: AD-005's atomicity only holds if *all* config maps flip on one `active_slot` write; a rule
stage outside the slot discipline would reintroduce the hybrid old/new window SLRD eliminated. Fail-closed
is the product's §11.3 posture.

**Acceptance Criteria**:

1. WHEN the rule block map is defined THEN it SHALL be a **slotted config map** (double-buffer, selected
   by the pinned `active_slot`, same mechanism as `service_map`) keyed by `service_id`, holding
   `rule_count` + up to 16 rule entries whose fields mirror the SRL `allow_rule` contract. `(ARL-18)`
2. WHEN any rule-stage map lookup fails (slot inner missing, unexpected read error) THEN the system SHALL
   fail closed with `DR_MAP_ERROR` — never treating a broken map as a pass (distinct from a clean
   block-absent = `not_allowed`, ARL-06). `(ARL-19)`
3. WHEN `not_allowed` / `rate_limit_drop` verdicts are produced THEN they SHALL be recorded via
   `record_drop()` at the **frozen ABI indices 9 and 10** — exact per-CPU counts plus rate-limited
   ringbuf sampling, no enum change, no renumbering, `dpstat` decodes them with zero changes. `(ARL-20)`
4. WHEN the gateway loads THEN the loader/seed helper (SLRD's, extended) SHALL be able to populate rule
   blocks alongside services, so the stage is independently loadable, testable, and demoable before the
   M4 worker exists (same interim-writer posture as D-SLRD-1). `(ARL-21)`
5. WHEN the rule stage is inserted THEN the existing dp-unit suite (34 cases) SHALL be migrated: cases
   whose enabled-service packets previously expected unconditional `XDP_REDIRECT` seed a match-all rule
   (or updated expectations), and the full suite passes. `(ARL-22)`
6. WHEN the program is built THEN it SHALL still load in **native/DRV mode fail-loud** (the bounded rule
   loop passes the verifier); admitted packets reach `redirect_out()` byte-for-byte unchanged (TTL/
   checksum untouched — redirect semantics inherited, not modified). `(ARL-23)`
7. WHEN the admit path is wired THEN the point between quota-admit and `redirect_out()` SHALL be a
   **marked seam** (comment + stable function boundary) where M3 #4's fairness ladder inserts, mirroring
   how packet-parse marked the seams SLRD later replaced. `(ARL-24)`
8. WHEN the feature lands THEN `TESTING.md`'s data-plane section SHALL document the rule-seeding and
   deterministic-bucket conventions for M3 stages. `(ARL-25)`

**Independent Test**: `make bpf skel loader` builds; loader attaches natively with seeded service + rules;
flipping the seeded slot's inner block changes verdicts only via one `active_slot` write; `make test` —
full migrated suite + new ARL cases pass; `dpstat counters` shows `not_allowed`/`rate_limit_drop` rows
counting after test traffic.

---

## Edge Cases

- WHEN a service's block holds exactly 16 rules and none match THEN the loop SHALL visit all 16 and drop
  `not_allowed` (cap is inclusive, no over-read).
- WHEN an ICMP packet is evaluated against a `tcp`/`udp` rule with port ranges THEN the protocol test
  SHALL reject it before any port comparison (ICMP's zeroed `pkt_meta` ports never accidentally match).
- WHEN an `icmp`/`any` rule reaches the data-plane THEN its port ranges are NULL by control-plane
  construction (SRL model); the data-plane SHALL treat any non-NULL range on such a rule as wildcard
  rather than undefined behavior.
- WHEN two rules overlap THEN the lower `priority` wins by construction — overlap is legal and advisory
  (SRL-18 warns at CRUD time); no data-plane dedup.
- WHEN ARP frames traverse the gateway THEN they SHALL be unaffected — ARP redirects before service
  lookup and never reaches the rule stage.
- WHEN the packet's service was matched but `pkt_meta.service_id` has no rule block in the pinned slot
  THEN default-deny `not_allowed` (ARL-06) — e.g., the seed populated `service_map` but not the block.
- WHEN a burst arrives within one refill window on a single CPU THEN admits SHALL never exceed the
  bucket's burst capacity for that window (no unbounded burst on idle buckets).
- WHEN `rule_count` in the map exceeds 16 (corrupt/foreign writer) THEN the loop clamps to 16 (ARL-07),
  never trusting the map for loop bounds.

---

## Gray Areas (RESOLVED — see `context.md`, D-ARL-1..2)

**GA-1 → D-ARL-1: `any` = {tcp, udp, icmp} strictly.** Non-TCP/UDP/ICMP IPv4 protocols (GRE, ESP, …)
are unmatchable → always `not_allowed`; v1 does not carry tunnel/IPsec traffic (documented product
statement; explicit `gre`/`esp` protocol values captured as a deferred idea).

**GA-2 → D-ARL-2: buckets reset on config swap.** Positional/per-version keying; every apply re-grants
full burst to the service's rules (bounded, brief). No rule-identity plumbing in the M4 build contract.

Original options considered:

### GA-1: What does `protocol = any` match — and can non-TCP/UDP/ICMP IPv4 traffic ever pass?

PRD §6.4 says protocol is "TCP, UDP, ICMP, or ANY **within the supported range**" — ambiguous. Packet
parse deliberately lets other IPv4 protocols (GRE, ESP, …) continue down the pipeline (A-PKT-5), so the
rule stage is where their fate is decided:

- **(a) `any` = any IPv4 protocol.** An `any` rule (ports always NULL) admits GRE/ESP/etc. Tenants who
  need tunnels can allow them; but a broad `any` rule silently allows *every* protocol.
- **(b) `any` = any of {tcp, udp, icmp}.** Other IPv4 protocols are unmatchable → always `not_allowed`.
  Strictest default-deny; means v1 cannot protect tunnel/IPsec traffic at all (a product statement, and
  A-PKT-5's pass-through becomes moot).

Affects: ARL-03, onboarding docs, and whether `not_allowed` floods from tunnel traffic are expected.

### GA-2: Bucket identity across config swaps — do rule edits reset quotas?

`rate_limit_state` is unslotted runtime state, but rules live in slotted config. When the M4 worker
rebuilds a service's block and flips the slot, how do in-flight buckets map to the new rules?

- **(a) Positional/versioned buckets — swap resets the service's buckets.** Key = (service, slot-position
  or version). Simple, no identity plumbing; every apply of *any* rule/list edit on the service briefly
  re-grants full burst to all its rules (a flood gets one extra burst per apply).
- **(b) Stable rule identity — buckets survive swaps.** The block carries a stable per-rule id (e.g., the
  DB row id) and buckets key on it. Edits to *other* rules preserve a flooding rule's exhausted state;
  costs identity plumbing through the M4 build contract and a larger bucket keyspace.

Affects: ARL-16, the map contract M4 must populate, and observable behavior during config churn under
attack. (The per-CPU split strategy itself — full-rate-per-CPU vs rate/nCPU — is a Design decision,
A-ARL-5, not a gray area: it has an established precedent in AD-017's sampling bucket.)

---

## Assumptions (flagged, not user-blocking)

- **A-ARL-1:** ICMP rules match the ICMP protocol as a whole; no type/code filtering in v1.
- **A-ARL-2:** Port ranges are inclusive; the control-plane guarantees `lo ≤ hi` within 0..65535
  (SRL-19); NULL = wildcard per dimension.
- **A-ARL-3:** Quota `0` = explicit block (always `rate_limit_drop` on match); NULL = unlimited. The
  control-plane currently permits 0 — no API change requested.
- **A-ARL-4:** Refill uses `bpf_ktime_get_ns` lazy refill on access (pattern proven by `sample.h`).
- **A-ARL-5:** The per-CPU budget-split strategy (full-rate-per-CPU vs rate÷nCPU vs hybrid) and its
  documented deviation bound are decided at Design; ARL-14 only requires the bound exist and be stated.
- **A-ARL-6:** The seed helper remains the only rule-block writer until M4; its format is a test/dev
  convenience, not the M4 contract (which is the map layout itself).
- **A-ARL-7:** Admitted traffic redirects immediately; the fairness ladder (M3 #4) inserts later at the
  ARL-24 seam. Until then "admit" = redirect.
- **A-ARL-8:** `pkt_meta` grows a matched-rule field (positional identity per D-ARL-2) within the existing
  struct-growth conventions; `test_meta_map` exposes it.

---

## Requirement Traceability

| Requirement ID | Story | Refs | Phase | Status |
| --- | --- | --- | --- | --- |
| ARL-01 | P1: First-match verdict | §8.1/8.2, AD-005 pin | Tasks | In Tasks |
| ARL-02 | P1: First-match verdict | §6.4, AD-004 | Tasks | In Tasks |
| ARL-03 | P1: First-match verdict | §6.4, D-ARL-1 | Tasks | In Tasks |
| ARL-04 | P1: First-match verdict | §6.4, SRL model | Tasks | In Tasks |
| ARL-05 | P1: First-match verdict | §6.4 (`enabled`) | Tasks | In Tasks |
| ARL-06 | P1: First-match verdict | §8.2 `not_allowed` | Tasks | In Tasks |
| ARL-07 | P1: First-match verdict | §6.4 `rule_count` early-exit | Tasks | In Tasks |
| ARL-08 | P1: First-match verdict | test hooks (D-PKT-4 pattern) | Tasks | In Tasks |
| ARL-09 | P1: Rate-limit | §6.4 pps/bps | Tasks | In Tasks |
| ARL-10 | P1: Rate-limit | AD-004/BL-05 terminal | Tasks | In Tasks |
| ARL-11 | P1: Rate-limit | SRL nullable pps/bps | Tasks | In Tasks |
| ARL-12 | P1: Rate-limit | quota independence | Tasks | In Tasks |
| ARL-13 | P1: Rate-limit | §6.4/§13 no per-source state | Tasks | In Tasks |
| ARL-14 | P1: Rate-limit | §6.4 error bound, A-ARL-5 | Tasks | In Tasks |
| ARL-15 | P1: Rate-limit | A-ARL-3 | Tasks | In Tasks |
| ARL-16 | P1: Rate-limit | §8.3 unslotted, D-ARL-2 | Tasks | In Tasks |
| ARL-17 | P1: Rate-limit | AD-017 determinism pattern | Tasks | In Tasks |
| ARL-18 | P1: Config map & migration | §8.3 `rule_block_map`, AD-005 | Tasks | In Tasks |
| ARL-19 | P1: Config map & migration | §11.3 fail-closed | Tasks | In Tasks |
| ARL-20 | P1: Config map & migration | AD-016/AD-017 ABI | Tasks | In Tasks |
| ARL-21 | P1: Config map & migration | D-SLRD-1 seed posture | Tasks | In Tasks |
| ARL-22 | P1: Config map & migration | suite migration (SLRD precedent) | Tasks | In Tasks |
| ARL-23 | P1: Config map & migration | native-mode mandate, §12.4 | Tasks | In Tasks |
| ARL-24 | P1: Config map & migration | M3 #4 seam | Tasks | In Tasks |
| ARL-25 | P1: Config map & migration | A-PKT-2 TESTING.md convention | Tasks | In Tasks |

**Coverage:** 25 total, 25 mapped to tasks (T1–T5, `tasks.md`), 0 unmapped.

---

## Success Criteria

- [ ] A service with rules `[p10: udp dst 53 pps=Q, p20: tcp dst 80]` under `BPF_PROG_TEST_RUN`: DNS-shaped
      UDP admits until Q then drops `rate_limit_drop` (index 10 exact); TCP:80 admits; TCP:443 drops
      `not_allowed` (index 9 exact); none of the overflow reaches p20.
- [ ] A service with zero rules drops 100% of its traffic as `not_allowed` — default-deny demonstrated.
- [ ] Full migrated dp-unit suite + new ARL cases pass via `make test`; `make bpf skel loader dpstat`
      builds; the program loads native/DRV fail-loud.
- [ ] `dpstat counters` shows live `not_allowed` / `rate_limit_drop` counts with no `dpstat` code change.
- [ ] Deterministic-bucket mode makes rate-limit tests exact (no flaky wall-clock assertions).
