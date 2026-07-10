# Fairness & Bandwidth Reservation (§8.4) Specification

**Milestone:** M3 — Policy enforcement & fairness
**Feature #4 of M3** (final M3 feature — the committed/burst/node admit ladder + ingress-cost cap;
completes the §8.2 verdict pipeline and the M3 milestone target: *flooding service A never starves
service B's committed bandwidth*)
**Category ID:** FAIR
**Status:** Spec + context complete (approved 2026-07-10) → Design (2 gray areas resolved: D-FAIR-1, D-FAIR-2)
**Discuss context:** `.specs/features/fairness-bandwidth/context.md` (D-FAIR-1..2, A-FAIR-1..8; AD-024)
**Depends on:**
- **Allow-rule matching & rate-limit** (`.specs/features/allow-rule-ratelimit/`) — owns the
  **ARL-24 seam**: `admit_clean()` (`rules.h`) is the marked insertion point this feature's admit
  ladder replaces, between a rule-quota admit and `redirect_out()`. Also reuses AD-019's bucket
  machinery precedents (`rl_bucket` shape, lazy version reset D-ARL-2, rate÷nCPU split,
  `test_no_refill` + CPU-pinned deterministic testing). **Executed (VERIFIED).**
- **Whitelist/VIP (scoped) & VIP ceiling** (`.specs/features/whitelist-vip/`) — owns the **WLV-24
  seam A** (immediately after the service-enabled check, *before* `whitelist_stage`) where the
  ingress-cost cap inserts, and the structural guarantee that the VIP admit path calls
  `redirect_out()` directly and **never enters this feature's ladder** (§8.4.6, AD-021).
  **Executed (VERIFIED).**
- **Blacklist (bloom + LPM)** (`.specs/features/blacklist-filters/`) — the deny-filter stage
  upstream of the rule stage; not consumed directly, but it shares the hot-path files and defines
  the dp-unit baseline this feature builds on. **Tasks APPROVED — this feature requires
  blacklist-filters executed first** (A-FAIR-1; post-BLK baseline).
- **Drop-reason counters** (`.specs/features/drop-reason-counters/`) — consumes the frozen index
  ABI: `DR_SERVICE_CEILING_DROP = 11`, `DR_CONGESTION_DROP = 12`, `DR_INGRESS_CAP_DROP = 13` are
  already reserved (reading 0 until wired here) plus the fused `record_drop(meta, reason)` helper.
  **Executed (VERIFIED).** After this feature, every one of the 16 §9.2 reasons is wired — M3
  leaves no dormant index.
- **Service lookup & transparent redirect** (`.specs/features/service-lookup-redirect/`) — the
  pinned `active_slot`, `pkt_meta.service_id`, the `ARRAY_OF_MAPS` double-buffer config pattern,
  `redirect_out()`, and the D-SLRD-1 seed-helper interim-writer posture. **Executed (VERIFIED).**
- **Service, rule & list management (API)** (`.specs/features/service-rule-list/`) — the
  source-of-truth **`ServicePlan`** rows: `committed_clean_gbps ≤ ceiling_clean_gbps` (SRL-03),
  admin-only sizing (SRL-07/A-SRL-1), `committed == 0` legal = best-effort-only (SRL-43),
  oversubscription is a **warning, not a block** (SRL-36, `Σ committed` vs `node_clean_capacity`,
  A-SRL-4). As with ARL/WLV/BLK the dependency is **contractual, not a code import** — this
  feature never reads Postgres; the M4 worker builds the rate config from those rows.

**Decisions already made (bind this spec):**
- **PRD §8.4 / TDD §4.4 (CM-04):** every service always receives its `committed_clean_gbps`; a
  flooded neighbor must never pull another service below its commitment on the shared data-plane.
  Three mechanisms, all in this feature: the 2-tier per-service token bucket, the node headroom
  bucket, and the per-service ingress-cost cap.
- **TDD §4.3/§4.4 (bucket accuracy posture):** the *committed* bucket uses a **global map +
  `bpf_spin_lock`** — exact regardless of RSS/CPU distribution (contention is bounded by the low
  committed rate, not the attack rate). The *burst* bucket (`ceiling − committed`) and the *node
  headroom* bucket are **per-CPU** (documented error accepted). `service_agg_rate_state`,
  `node_burst_state`, `service_ingress_cap_state` are **runtime-state maps — never slotted**
  (§8.3/AD-005).
- **TDD §4.4 admit ladder (clean non-VIP, pre-redirect):** committed bucket has tokens → admit
  (clean-committed). Else service burst bucket → if empty, `service_ceiling_drop`. Else node
  headroom bucket → if empty, `congestion_drop`; else admit (clean-burst). **Burst packets draw
  from both** the service burst **and** node headroom buckets; **committed packets skip the node
  bucket entirely.**
- **TDD §4.4 ingress-cost cap:** immediately **after service match, before expensive lookups**, a
  raw pps/bps cap = `k × ceiling` (k≈2–4); over-cap = cheap early drop `ingress_cap_drop`. Keyed
  by **destination service** (never source) ⇒ immune to source spoofing (§13: no per-source-IP
  state on the hot path).
- **§8.4.6 / AD-021 (structural):** whitelist/VIP traffic uses its own VIP ceiling and **never
  enters this admit ladder** — the WLV stage's VIP admit calls `redirect_out()` directly.
- **AD-016/AD-017:** drop-reason indices are frozen (11/12/13 reserved for exactly these three
  reasons); `counter_map` stays a pure drop-reason ABI; `dpstat` decodes new nonzero rows with
  zero code changes.
- **AD-004 (ARL):** the per-rule rate-limit verdict is terminal and upstream — a packet reaches
  this ladder only after passing its matched rule's quota; the ladder is an *additional* gate,
  never a replacement.
- **PRD §7.2 / SRL-36:** `Σ committed_clean_gbps ≤ node_clean_capacity` is control-plane-warned
  (not blocked); `node_clean_capacity` is a **node config value**, not a Postgres entity
  (A-SRL-4).
- **PRD §15:** committed bandwidth = hard guarantee per service even when a neighbor is flooded,
  verified by the fairness test; node target ≥ 40 Gbps / 20 Mpps.

## Problem Statement

After ARL/WLV/BLK, the pipeline enforces *policy* (rules, scoped bypass, deny lists) but nothing
enforces *capacity sharing*: a single flooded service can consume the whole node — its traffic,
having passed a generous rule quota, redirects without limit, starving every neighbor's clean
bandwidth and burning classification CPU that other services paid for. PRD §8.4 closes this with a
per-service committed/burst admit ladder, a node headroom bucket, and a cheap ingress-cost cap in
front of the expensive lookups. This feature builds all three, wires the last three frozen drop
reasons (11/12/13), and delivers the M3 milestone gate: the fairness test.

## Goals

- [ ] A service flooded past its ceiling loses only its **own** burst/committed budget: every other
      service's committed-rate traffic keeps admitting bit-for-bit (CM-04 hard guarantee,
      demonstrated by a deterministic fairness test).
- [ ] Clean non-VIP traffic admits through the two-tier ladder — committed (exact, spin-locked
      global bucket) always admits; burst (`ceiling − committed`, per-CPU) admits only while node
      headroom (`node_clean_capacity − Σ committed`, per-CPU) has tokens; the three over-limit
      outcomes drop with the correct frozen reasons: `service_ceiling_drop` (11),
      `congestion_drop` (12).
- [ ] Raw ingress to a matched service is capped at `k × ceiling` immediately after service match
      — over-cap traffic drops `ingress_cap_drop` (13) **before** whitelist/deny/rule work,
      bounding the CPU a flooded service can consume, keyed by destination only (spoof-immune).
- [ ] Per-service rates arrive via a slotted config map built from `ServicePlan` (layout = the M4
      build contract); the three bucket-state maps are unslotted runtime state; the seed helper can
      populate everything so the ladder is loadable/testable/demoable before M4 (D-SLRD-1).
- [ ] The post-BLK dp-unit baseline still passes (default seed = generous plan, verdicts
      unchanged); the program still loads native/DRV fail-loud; the §8.4.6 residual limitation is
      documented, not hidden.

## Out of Scope

Explicitly excluded — owned by other features. Documented to prevent scope creep.

| Feature | Reason |
| --- | --- |
| The M4 worker that builds the fairness rate config from `ServicePlan` rows, verifies, and flips `active_slot` | M4. This feature owns the read side + extends the seed helper (interim writer, D-SLRD-1). |
| `ServicePlan` CRUD, `committed ≤ ceiling` validation, admin-only sizing, oversubscription warning | Done — service-rule-list (SRL-03/07/36/43, control-plane). |
| VIP ceiling enforcement (`vip_ceiling_drop`) | Done — whitelist-vip (WLV). VIP traffic never enters this ladder (§8.4.6). |
| Per-rule pps/bps rate limits (`rate_limit_drop`) | Done — allow-rule-ratelimit (ARL). Upstream of this ladder. |
| Per-service **clean byte counters** for billing / p95 metering / `BillingUsage` | M5 *Chargeback metering* (AD-006). This feature admits clean traffic; exact billing counters are M5's surface. |
| Fairness-breach / congestion / cap-storm **alerting** and dashboards | M6 *Alerting* + M5 *Telemetry* ("Committed honored per service" metric, §9.1). This feature provides the counters they consume. |
| Weighted/proportional burst sharing between services when node headroom is contended | Not in PRD v1 — §8.4 sheds **all** burst on headroom exhaustion; refinement = GA idea. |
| SYN-cookie / port-scan detection to harden the cap story | GA (BL-04, deferred). |
| HA / scale-out / absorption capacity claims | CM-01/CM-06 (M7 / product positioning). §8.4.6 documents the single-node residual limit. |
| Control-plane surface for `node_clean_capacity` / `k` | Node config values (A-SRL-4); v1 delivery per GA-2 resolution — no Postgres entity/CRUD invented here. |

---

## User Stories

### P1: Two-tier committed/burst bucket per service (`service_ceiling_drop`) ⭐ MVP

**User Story**: As a tenant with a paid `committed_clean_gbps`, I want my clean traffic admitted at
my committed rate no matter what any other service is doing, and burst headroom up to my ceiling
when the node has room — so the bandwidth I pay for is a hard guarantee, not a best effort.

**Why P1**: This is CM-04 — the SLA-bearing core of the whole milestone. The M3 target ("fairness
test passes") is untestable without it, and M5 chargeback (`billed = max(committed, p95)`) is
commercially incoherent if committed is not actually guaranteed.

**Acceptance Criteria**:

1. WHEN a clean non-VIP packet passes its matched rule's quota THEN the admit ladder SHALL run at
   the **ARL-24 seam** (`admit_clean()`, between the rule-stage admit and `redirect_out()`); the
   VIP admit path SHALL remain untouched — whitelisted traffic never enters the ladder (§8.4.6
   structural, AD-021). `(FAIR-01)`
2. WHEN the ladder runs THEN it SHALL first draw the packet's cost from the service's **committed
   bucket** — refilled at `committed_clean_gbps` (converted to bytes/sec at build), accounted
   **exactly** via a global (non-per-CPU) bucket protected by `bpf_spin_lock` so accuracy is
   independent of RSS/CPU distribution — and on success SHALL admit immediately (clean-committed),
   **skipping both** the burst and node buckets. `(FAIR-02)`
3. WHEN the committed bucket lacks tokens THEN the ladder SHALL try the service's **burst bucket**
   — refilled at `ceiling_clean_gbps − committed_clean_gbps`, per-CPU with a documented deviation
   bound (AD-019 rate÷nCPU posture: node aggregate never exceeds the configured burst rate).
   `(FAIR-03)`
4. WHEN the burst bucket is also empty THEN the packet SHALL drop `XDP_DROP` with reason
   `service_ceiling_drop` (frozen ABI index 11) — terminal, no fall-through, regardless of node
   headroom. `(FAIR-04)`
5. WHEN service A receives traffic past its ceiling THEN A's flood SHALL consume tokens only from
   **A's** buckets — service B's committed-rate traffic SHALL keep admitting unaffected (the
   fairness property; buckets are keyed per service). `(FAIR-05)`
6. WHEN a plan has `committed == 0` (best-effort-only, SRL-43) THEN all of that service's clean
   traffic SHALL be burst (subject to node headroom); WHEN `committed == ceiling` THEN the burst
   rate SHALL be 0 and traffic beyond committed drops `service_ceiling_drop`. `(FAIR-06)`
7. WHEN the ladder reads its per-service rates THEN they SHALL come from a **slotted config map**
   (built from `ServicePlan`, read via the slot pinned at ingress); a structurally missing entry
   for a matched enabled service, or a failed read of a must-exist map, SHALL fail closed
   `DR_MAP_ERROR` (WLV-07/ARL-19/BLK-07 posture); the bucket-state maps SHALL be **unslotted
   runtime state** whose buckets lazily reset on config-version change (D-ARL-2 posture — one
   burst re-grant per apply, bounded and accepted). `(FAIR-07)`
8. WHEN the ladder decides (committed-admit, burst-admit, ceiling-drop, congestion-drop) THEN the
   outcome SHALL be recorded in `pkt_meta` (observable via `test_meta_map` under
   `-DPKT_TEST_HOOKS`). `(FAIR-08)`

**Independent Test**: Seed A (committed 1 Gbps, ceiling 2 Gbps) and B (committed 1 Gbps, ceiling
1 Gbps); deterministic mode (`test_no_refill`, CPU-pinned): A admits exactly its committed tokens
as clean-committed, then exactly its burst tokens as clean-burst, then drops
`service_ceiling_drop` (counter index 11); interleaved B packets admit clean-committed throughout;
B with `committed == ceiling` never takes the burst path.

---

### P1: Node headroom bucket (`congestion_drop`) ⭐ MVP

**User Story**: As the platform operator, I want burst traffic across all services jointly capped
at the node's spare clean capacity, so that overlapping bursts can never oversubscribe the node —
burst sheds first, and committed traffic always has the room §7.2 reserved for it.

**Why P1**: Without it, `Σ ceiling > node_clean_capacity` (normal overcommit) lets simultaneous
bursts exceed what the node can cleanly forward — the committed guarantee of story 1 would hold at
the buckets but break at the wire. §8.4 mechanism 2 is what makes overcommitted ceilings safe.

**Acceptance Criteria**:

1. WHEN the node headroom bucket is defined THEN it SHALL refill at `node_clean_capacity −
   Σ committed_clean_gbps` (bytes/sec; floored at 0 when oversubscribed), per-CPU (§8.3), as the
   single shared budget for **all** burst admits on the node. `(FAIR-09)`
2. WHEN a packet admits from the burst path THEN it SHALL have drawn its cost from **both** the
   service burst bucket **and** the node headroom bucket (clean-burst requires both); committed
   packets SHALL never touch the node bucket. `(FAIR-10)`
3. WHEN the node headroom bucket lacks tokens THEN the burst packet SHALL drop `XDP_DROP` with
   reason `congestion_drop` (frozen ABI index 12) — all-burst shedding, while every service's
   committed traffic keeps flowing untouched. `(FAIR-11)`
4. WHEN `node_clean_capacity` and `Σ committed` are delivered THEN their v1 source SHALL follow the
   GA-2 resolution (node config via seed/loader — D-SLRD-1 posture; the M4 worker recomputes the
   headroom rate whenever plan changes shift `Σ committed`); the derivation SHALL be documented.
   `(FAIR-12)`
5. WHEN the control plane has allowed oversubscription (`Σ committed > node_clean_capacity`,
   SRL-36 warning) THEN the headroom rate SHALL floor at 0 — every burst packet drops
   `congestion_drop`, committed admission is unchanged, and the behavior SHALL be documented as
   the deliberate consequence of the §7.2 constraint being warned-not-blocked. `(FAIR-13)`

**Independent Test**: Deterministic mode with node headroom seeded to N tokens and services A/B
each holding burst tokens > N: burst admits across A+B total exactly N (each drew node tokens),
then both drop `congestion_drop` (index 12) while committed packets from either service still
admit; reseed with headroom rate 0 (oversubscription case) — first burst packet drops.

---

### P1: Ingress-cost cap per service (`ingress_cap_drop`) ⭐ MVP

**User Story**: As the platform operator, I want a flooded service's **raw** packet stream capped
cheaply before the expensive per-packet work, so an attack on one service burns a bounded slice of
node CPU and the classification capacity other tenants' traffic depends on stays available.

**Why P1**: §8.4 mechanism 3. The bucket ladder (stories 1–2) protects *bandwidth* but runs at the
**end** of the pipeline — every flood packet still pays whitelist + deny-filter + rule cost to
reach it. The cap is the CPU-side half of fairness, and the §8.4.6 residual-limit story is only
honest with it in place.

**Acceptance Criteria**:

1. WHEN a packet matches an **enabled** service THEN the ingress-cost cap SHALL run at **WLV-24
   seam A** — immediately after the service-enabled check, **before** `whitelist_stage` and every
   other lookup — and SHALL count **every** such packet against the service's cap regardless of
   the packet's eventual verdict (raw ingress cost, not clean throughput). `(FAIR-14)`
2. WHEN the cap is evaluated THEN its budget SHALL be `k × ceiling_clean_gbps` per service
   (dimensions and derivation per the **GA-1** resolution; k default ≈ 2–4, node-tunable at load
   per the AD-019 `rl_ncpus` rodata posture, never per-packet-configurable), accounted in a
   per-CPU bucket (§8.3 `service_ingress_cap_state`). `(FAIR-15)`
3. WHEN the cap is exceeded THEN the packet SHALL drop `XDP_DROP` with reason `ingress_cap_drop`
   (frozen ABI index 13) — a terminal early drop that performs no whitelist, deny-filter, or rule
   work. `(FAIR-16)`
4. WHEN the cap state is keyed THEN the key SHALL be the **destination service only** — no
   per-source state of any kind (§13/§11.1) — so spoofed/rotating sources cannot evade or inflate
   it. `(FAIR-17)`
5. WHEN traffic is under the cap THEN it SHALL continue into the whitelist stage completely
   unchanged (the cap is a gate, never an admit-to-redirect); because the cap sits **upstream of**
   the whitelist, a would-be-VIP packet arriving during an over-cap flood MAY be ingress-cap
   dropped — this precedence SHALL be documented (a VIP ceiling ≤ ceiling < k × ceiling, so
   compliant VIP traffic is unaffected in normal operation). `(FAIR-18)`
6. WHEN the cap's per-service budget cannot be read structurally (missing entry for a matched
   enabled service, must-exist map read failure) THEN the system SHALL fail closed `DR_MAP_ERROR`
   (FAIR-07 posture). `(FAIR-19)`

**Independent Test**: Deterministic mode, service A capped at C tokens: C packets pass into the
whitelist stage (pkt_meta shows stage progression), packet C+1 drops `ingress_cap_drop` (index 13)
with `pkt_meta` showing no whitelist/deny/rule stage ran; a whitelisted source's packets under the
cap still VIP-redirect; the same whitelisted source over the cap drops at index 13 (documented
precedence); a spoofed-source flood to A never creates per-source state (map inventory unchanged).

---

### P1: Maps, frozen-ABI wiring, seed, fairness gate & docs ⭐ MVP

**User Story**: As the gateway, I want the fairness config delivered through the same slotted
contract as every other config map and the ladder proven by a repeatable fairness test, so the M4
worker can swap plan changes atomically, operators see the three new drop reasons in existing
tooling with zero changes, and the M3 milestone gate is demonstrable on demand.

**Why P1**: AD-005's atomicity must cover plan rates like everything else; the fairness test **is**
the M3 exit criterion (§15: "Test fairness 8.4"); the spin-lock committed bucket is the one novel
kernel mechanism in this feature and must be de-risked fail-fast.

**Acceptance Criteria**:

1. WHEN the maps are defined THEN the per-service rate config (committed/burst/cap budgets) SHALL
   be a **slotted config map** (double-buffer, selected by the pinned `active_slot`) whose
   concrete layout — decided at Design — **is the M4 build contract**; `service_agg_rate_state`,
   `node_burst_state`, and `service_ingress_cap_state` SHALL be **unslotted runtime maps**
   (§8.3). `(FAIR-20)`
2. WHEN `service_ceiling_drop` / `congestion_drop` / `ingress_cap_drop` verdicts are produced THEN
   they SHALL be recorded via `record_drop()` at frozen ABI indices **11 / 12 / 13** — exact
   per-CPU counts plus rate-limited ringbuf sampling; no enum change, no renumbering; `dpstat`'s
   drop-reason output decodes them with **zero code changes**. With this feature, all 16 §9.2
   reasons are live. `(FAIR-21)`
3. WHEN the committed bucket is implemented THEN the `bpf_spin_lock` global-bucket mechanism SHALL
   be proven at the **first build/load gate** (verifier acceptance in XDP on the target kernel —
   fail-fast, not assumed); a documented, verdict-preserving fallback ladder (Design-owned, e.g.
   atomics-based global bucket with bounded skew) SHALL exist in case of rejection, with the
   accuracy deviation documented. `(FAIR-22)`
4. WHEN the gateway loads with the **default seed** THEN plan rates SHALL be generous enough that
   every post-BLK baseline case keeps its exact verdict (baseline traffic admits clean-committed;
   caps never trigger) — the dp-unit suite passes unchanged and the live smoke keeps its current
   behavior; env-driven seed values (D-SLRD-1 posture, GA-2) SHALL make constrained plans
   demoable without the M4 worker. `(FAIR-23)`
5. WHEN the fairness gate runs THEN a **deterministic dp-unit fairness scenario** SHALL
   demonstrate the M3 milestone target — service A flooded past its ceiling (drops at indices
   13/11/12 as configured) while service B's committed-rate traffic admits **100%** — and a gated
   live-smoke variant SHALL demonstrate the same shape on the two-veth path. `(FAIR-24)`
6. WHEN bucket behavior is tested THEN the deterministic conventions SHALL reuse ARL's machinery
   (`test_no_refill`-style knob, CPU-pinned runner, quota == exact admit count) — no new test
   paradigm. `(FAIR-25)`
7. WHEN the program is built THEN it SHALL still load in **native/DRV mode fail-loud**; the
   post-BLK dp-unit baseline SHALL pass with no expectation changes beyond those FAIR-23 defines
   (none expected); new FAIR cases are additive. `(FAIR-26)`
8. WHEN the feature lands THEN `TESTING.md`'s data-plane section SHALL document fairness
   seeding/deterministic-ladder conventions, and `README`/docs SHALL document: the ladder
   semantics (committed exact / burst per-CPU / node shared), the `k` and `node_clean_capacity`
   node-config knobs (GA-1/GA-2 resolutions), the VIP-under-cap precedence (FAIR-18), and the
   **§8.4.6 residual limitation** verbatim in spirit: committed clean bandwidth is hard-guaranteed,
   but under a PPS flood exceeding the node's total classification capacity the ingress cap
   *bounds* rather than *eliminates* CPU contention — a single-node physical limit
   (CM-01/CM-06), never hidden inside SLA claims. `(FAIR-27)`

**Independent Test**: `make bpf skel loader dpstat` builds; loader attaches natively with the
default seed and the post-BLK suite + live smoke pass unchanged; the spin-lock de-risk case loads
(or the documented fallback engages fail-fast); the fairness scenario passes with B at 100%
committed admission while A floods; `dpstat counters` shows live rows at indices 11/12/13 after
constrained-seed test traffic.

---

## Edge Cases

- WHEN service B's committed traffic arrives while service A floods at line rate THEN B admits at
  full committed rate (FAIR-05) — the committed bucket's spin-lock contention is bounded by
  *committed* rates (low, paid-for), never by A's attack rate (A's flood dies at A's cap/buckets).
- WHEN a packet passes its rule quota but the service is at ceiling THEN it drops
  `service_ceiling_drop` — rule quota (ARL) and the ladder are independent serial gates; passing
  one never implies the other.
- WHEN `committed == ceiling` THEN the burst path is structurally dead for that service (rate 0)
  and node headroom is never consulted for its traffic.
- WHEN `ceiling == 0` (legal only with `committed == 0`, SRL-03) THEN the ingress cap budget is 0 —
  effectively all traffic to that service drops `ingress_cap_drop` at seam A; documented as the
  degenerate "no clean bandwidth purchased" state.
- WHEN VIP traffic flows on a service under normal load THEN it bypasses the ladder entirely
  (§8.4.6) but **is** counted by the upstream ingress cap like all traffic (FAIR-14/18).
- WHEN ARP frames traverse the gateway THEN they redirect before service match and never touch the
  cap or ladder (unchanged from SLRD).
- WHEN traffic to a missing or disabled service arrives THEN `service_miss`/`service_disabled`
  verdicts stand — the cap and ladder run only on the enabled-service path.
- WHEN the node headroom bucket has tokens but the service burst bucket is empty THEN the drop is
  `service_ceiling_drop`, not `congestion_drop` (service limit checked first — reason attribution
  follows the ladder order).
- WHEN a slot flip lands mid-flow THEN each packet uses its pinned slot's rates; buckets lazily
  reset on version change (D-ARL-2): one full-burst re-grant per apply, bounded by burst size.
- WHEN plan edits change `Σ committed` THEN the node headroom **rate** changes with the next
  config build (M4; seed in v1) — the runtime bucket itself is never slotted (FAIR-12/20).
- WHEN the deterministic fairness test runs on a multi-CPU host THEN CPU pinning keeps per-CPU
  bucket draws exact (AD-019 precedent); the committed bucket is exact by construction.
- WHEN `test_no_refill` freezes refills THEN committed/burst/node/cap buckets all honor it — quota
  value == exact admit count across all four (FAIR-25).

---

## Gray Areas (RESOLVED — see `context.md`, D-FAIR-1..2)

**GA-1 → D-FAIR-1: dual-dimension cap (bps + derived pps), k = 3 default** (option a): byte
budget = `k × ceiling` directly; packet budget = `k × ceiling ÷ reference_packet_size`
(documented, node-tunable, ~512 B — exact default at Design); k node-tunable at load, never
per-service in v1. Reuses ARL's dual `pps/bps` bucket shape.

**GA-2 → D-FAIR-2: env-driven `node_clean_capacity`, documented 40 Gbps default when unset**
(option a): D-SLRD-1 seed posture; headroom rate = `capacity − Σ committed` (floor 0), recomputed
by seed (v1) / M4 worker (authoritative) on plan changes; loader never fails on unset capacity —
mis-set blast radius is burst-only (committed skips the node bucket).

Original options considered:

### GA-1: Ingress-cost cap — dimensions & default `k`

TDD §4.4 says the cap is "raw **pps/bps** = `k × ceiling` (k≈2–4)" — but `ceiling_clean_gbps` is a
*bandwidth*, so a **pps** budget needs a derivation rule, and the dimension choice changes what the
cap actually protects:

- **(a) Dual-dimension (bps + derived pps), k = 3 default.** Byte budget = `k × ceiling` directly;
  packet budget = `k × ceiling ÷ reference_packet_size` (reference size documented and
  node-tunable, e.g. ~512 B). Protects both bandwidth *and* CPU: a 64-byte-packet PPS flood hits
  the pps cap long before the byte cap. Reuses ARL's dual `pps/bps` bucket shape verbatim.
  **Recommended** — CPU protection is the mechanism's stated purpose, and pps is the CPU cost
  dimension.
- **(b) bps-only, k = 3 default.** Literal `k × ceiling` bytes/sec; simplest possible cap. Weak
  against small-packet floods: at 10 Gbps ceiling × k=3, a 64 B flood can sustain ~44 Mpps of
  classification work without ever hitting the byte cap — the CPU-protection goal is largely
  unserved.
- **(c) bps + independent absolute pps knob per node.** Full operator control (`cap_pps` set
  explicitly per node), no derivation heuristic — but invents a second node-config surface and
  leaves the per-service proportionality (`k × ceiling`) only half-true.

Affects: FAIR-15, FAIR-24's flood shape, the node-config surface (FAIR-27 docs), and how honest
the §8.4.6 CPU story is.

### GA-2: `node_clean_capacity` v1 source & unset default

The headroom rate needs `node_clean_capacity` (and `Σ committed`) on a node with no control-plane
entity for it (A-SRL-4: "node config value"). v1 delivery and the unset default:

- **(a) Env-driven seed/loader value with a documented default (40 Gbps, §15 target) when unset.**
  `XDPGW_NODE_CLEAN_CAPACITY`-style env (D-SLRD-1 posture, like the WLV/BLK seeds); the seed/M4
  worker computes `capacity − Σ committed` into the headroom rate. Unset ⇒ documented 40 Gbps
  default — dev/baseline/smoke friendly. Safe direction: a wrong default only mis-sizes **burst**
  QoS; the committed guarantee never depends on the node bucket (committed skips it, and §7.2
  keeps `Σ committed ≤ capacity` control-plane-warned). **Recommended.**
- **(b) Mandatory env — loader fails loud when unset.** Strictest (no silently wrong capacity),
  consistent with the native-mode fail-loud ethos, but breaks the "default seed just works"
  posture every M2/M3 feature has kept for baseline and smoke, for a value whose mis-set blast
  radius is burst-only.
- **(c) Unset ⇒ node bucket disabled (unlimited headroom) + warning.** Never sheds burst in dev;
  but `congestion_drop` becomes dead code by default and the default posture silently disables a
  §8.4 mechanism — weakest.

Affects: FAIR-12/13, FAIR-23's default-seed story, loader/docs (FAIR-27), and M4's worker contract
for recomputing headroom on plan changes.

---

## Assumptions (flagged, not user-blocking)

- **A-FAIR-1:** This feature executes **after blacklist-filters completes** (last M3 feature;
  shared hot-path files; the baseline is post-BLK) — the spec avoids pinning test counts and
  references "the post-BLK baseline".
- **A-FAIR-2:** Per-service rates reach the kernel via a **new** slotted config map keyed by
  `service_id` (the 8-byte `service_val` is frozen; both spare pad bytes are already taken by
  `wl_flags`/`bl_flags`) — exact layout, and whether cap budgets share the map with
  committed/burst rates, are Design decisions that become the M4 build contract (FAIR-20). Gbps →
  bytes/sec conversion happens at build time (AD-019 `bps` precedent).
- **A-FAIR-3:** Packet cost = wire length (`data_end − data`), consistent with ARL's byte
  accounting.
- **A-FAIR-4:** All fairness buckets lazily reset on config-version change (D-ARL-2 posture); the
  committed bucket's version check rides inside its spin-lock critical section.
- **A-FAIR-5:** Per-CPU rate splits (burst, node headroom, ingress cap) follow AD-019's
  rate÷nCPU posture — node aggregate never exceeds the configured rate; deviation bounds
  documented at Design. The committed bucket needs no split (global + lock = exact).
- **A-FAIR-6:** Burst draw ordering across the two buckets (service-then-node vs. check-both-
  then-consume) and whether a node-miss refunds already-drawn service-burst tokens are Design
  decisions — per-CPU burst accounting is approximate by charter, and no FAIR requirement
  observes the difference.
- **A-FAIR-7:** `pkt_meta` grows a ladder-outcome field within existing struct-growth conventions
  (D-PKT-4/A-WLV-7/A-BLK-8 pattern); `test_meta_map` exposes it (FAIR-08).
- **A-FAIR-8:** `bpf_spin_lock` in XDP program context on the target kernel is the one novel
  kernel-semantics claim here — verified at Design (Knowledge Verification Chain) and de-risked
  fail-fast at the first load gate (FAIR-22), with a verdict-preserving fallback ladder.

---

## Requirement Traceability

| Requirement ID | Story | Refs | Phase | Status |
| --- | --- | --- | --- | --- |
| FAIR-01 | P1: Two-tier bucket | §8.4.1, ARL-24 seam, §8.4.6/AD-021 | Spec | Pending |
| FAIR-02 | P1: Two-tier bucket | §8.4/TDD 4.3 spin-lock committed | Spec | Pending |
| FAIR-03 | P1: Two-tier bucket | §8.4 burst tier, AD-019 split | Spec | Pending |
| FAIR-04 | P1: Two-tier bucket | §10.2 idx 11 | Spec | Pending |
| FAIR-05 | P1: Two-tier bucket | CM-04 isolation, §15 fairness | Spec | Pending |
| FAIR-06 | P1: Two-tier bucket | SRL-43 plan shapes | Spec | Pending |
| FAIR-07 | P1: Two-tier bucket | §8.3 slotted config, D-ARL-2, fail-closed | Spec | Pending |
| FAIR-08 | P1: Two-tier bucket | test hooks (D-PKT-4) | Execute | In progress (T1 field) |
| FAIR-09 | P1: Node headroom | §8.4.2 headroom rate, §7.2 | Spec | Pending |
| FAIR-10 | P1: Node headroom | §8.4 dual-draw burst | Spec | Pending |
| FAIR-11 | P1: Node headroom | §10.2 idx 12, committed skips | Spec | Pending |
| FAIR-12 | P1: Node headroom | **GA-2**, A-SRL-4, D-SLRD-1 | Spec | Pending |
| FAIR-13 | P1: Node headroom | SRL-36 oversubscription consequence | Spec | Pending |
| FAIR-14 | P1: Ingress cap | §8.4.3, WLV-24 seam A, raw cost | Execute | Verified (T2) |
| FAIR-15 | P1: Ingress cap | **GA-1** k×ceiling, AD-019 rodata posture | Execute | Verified (T2) |
| FAIR-16 | P1: Ingress cap | §10.2 idx 13, early terminal | Execute | Verified (T2) |
| FAIR-17 | P1: Ingress cap | §13/§11.1 destination-keyed | Execute | Verified (T2) |
| FAIR-18 | P1: Ingress cap | cap-before-whitelist precedence | Execute | Verified (T2) |
| FAIR-19 | P1: Ingress cap | fail-closed posture | Execute | Verified (T2) |
| FAIR-20 | P1: Maps, gate & docs | §8.3 map groups, AD-005, M4 contract | Execute | Verified (T1) |
| FAIR-21 | P1: Maps, gate & docs | AD-016/17 idx 11/12/13 | Execute | In progress (T2 idx 13) |
| FAIR-22 | P1: Maps, gate & docs | spin-lock de-risk, A-FAIR-8 | Execute | Verified (T1) |
| FAIR-23 | P1: Maps, gate & docs | D-SLRD-1 seed, post-BLK baseline | Spec | Pending |
| FAIR-24 | P1: Maps, gate & docs | §15 fairness test = M3 gate | Spec | Pending |
| FAIR-25 | P1: Maps, gate & docs | AD-019 deterministic conventions | Spec | Pending |
| FAIR-26 | P1: Maps, gate & docs | native mandate, baseline | Spec | Pending |
| FAIR-27 | P1: Maps, gate & docs | §8.4.6 residual, A-PKT-2 TESTING.md | Spec | Pending |

**Coverage:** 27 total, 0 mapped to tasks (Tasks phase pending), 27 unmapped ⚠️

---

## Success Criteria

- [ ] **The M3 fairness gate:** with A flooded past `k × ceiling`, B's committed-rate traffic
      admits 100% in the deterministic scenario (and the gated smoke variant) — CM-04 demonstrated
      end-to-end.
- [ ] A's flood produces the correct reason at each exhaustion point — `ingress_cap_drop` at the
      cap, `service_ceiling_drop` past burst, `congestion_drop` when only node headroom is out —
      visible live in `dpstat` at indices 13/11/12 with zero tooling changes.
- [ ] Committed accounting is exact (spin-locked global bucket loads and passes the de-risk case);
      burst/node/cap per-CPU deviation bounds are documented; node aggregate never exceeds
      configured rates.
- [ ] Plan changes flow through the slotted config contract: reseeding a slot and flipping
      `active_slot` changes ladder behavior via that single write, buckets self-reset (D-ARL-2).
- [ ] Default seed keeps the post-BLK baseline verdict-identical and the live smoke green;
      native/DRV load stays fail-loud; `make bpf skel loader dpstat` builds.
- [ ] GA-1/GA-2 resolutions reflected in FAIR-15/FAIR-12 behavior and the FAIR-27 docs; the
      §8.4.6 residual limitation is documented verbatim in spirit — after this feature, M3's
      pipeline (§8.2) is fully enforced with all 16 drop reasons live.
