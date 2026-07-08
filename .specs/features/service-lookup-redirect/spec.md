# Service Lookup & Transparent Redirect Specification

**Milestone:** M2 — Data-plane verdict pipeline (XDP core)
**Feature #2 of M2** (the redirect terminal of the §8.2 pipeline's clean path)
**Category ID:** SLRD
**Status:** Spec + context complete — awaiting approval → Design (3 gray areas resolved: D-SLRD-1..3)
**Depends on:**
- **Packet parse & fail-fast** (`.specs/features/packet-parse/`) — consumes the fully-populated stack
  `pkt_meta` (dst IPv4, proto, ports, `l3_off`/`l4_off`), the `enum drop_reason` + per-CPU `counter_map`
  + `record_drop()` contract, the native-mode loader, and the `BPF_PROG_TEST_RUN` harness. **Replaces**
  its two marked seams: the `XDP_PASS` service-lookup seam (PKT-15) and the ARP `XDP_PASS` seam (PKT-23/24).
  Requires packet-parse executed first.
- **Service, rule & list management (API)** (`.specs/features/service-rule-list/`) — the source-of-truth
  rows (`ProtectedService.cidr_or_ip` / `enabled`) that `service_map` is *conceptually* built from. This
  feature does **not** read Postgres; it defines the in-kernel map contract those rows will populate (via
  the M4 worker). Dependency is contractual, not a code import.

**Discuss context:** `.specs/features/service-lookup-redirect/context.md` (D-SLRD-1..3, A-SLRD-1..8) —
the 3 gray areas (GA-1 config-map ownership vs. the M4 gap, GA-2 redirect verification without a NIC,
GA-3 ARP bridge policy) are **resolved**; see the *Gray Areas* section below and `context.md`.

## Problem Statement

Packet parse ends at a placeholder: clean IPv4 is `XDP_PASS`-ed to the host stack and no service decision
is made. That is not a shippable gateway — the whole point of the §8.2 pipeline's clean path is to decide
*is this destination a declared, enabled service?* and, if so, **forward the frame `IN→OUT` as a
header-preserving L2 transparent bridge** (no TTL decrement, no checksum recompute, no kernel routing).
This feature closes that gap: it looks the destination IPv4 up in a `service_map`, produces the two
service verdicts (`service_miss` for undeclared destinations, `service_disabled` for declared-but-disabled
ones — a **drop-all**, not a pass-through, per AD-002), snapshots a consistent config view per packet
(`active_slot` pin), and redirects enabled-service traffic to `OUT` via a `tx_devmap`. It is the first
feature to introduce **config maps** and the **per-packet slot pin** the entire rest of M3 will read.

## Goals

- [ ] Every valid IPv4 packet leaving parse is resolved against `service_map` by **destination IPv4**
      (LPM, so a service declared on a CIDR matches every host in it), yielding exactly one of:
      enabled-hit → redirect, declared-but-disabled → `service_disabled` drop, no-match → `service_miss` drop.
- [ ] Clean traffic to an enabled service is `XDP_REDIRECT`-ed `IN→OUT` via `tx_devmap` with the L2/L3
      header **preserved byte-for-byte** — TTL unchanged, IPv4 checksum unchanged (PRD §6.2, §8.2, §12.4).
- [ ] Each packet snapshots `active_slot` **once** at ingress and uses that pinned slot for its lookup,
      so an in-flight slot flip never yields a hybrid old/new view (§8.1, AD-005) — the mechanism every
      M3 config-map lookup will reuse.
- [ ] `service_miss` and `service_disabled` are added to the shared `enum drop_reason` and recorded in
      `counter_map`, distinguishable to tests (extends packet-parse's minimal-counter contract, A-PKT-3).
- [ ] The ARP seam left by packet-parse is resolved per the chosen bridge policy (GA-3).
- [ ] The redirect decision is unit-testable via `BPF_PROG_TEST_RUN`, and real `IN→OUT` header-preserving
      forwarding is demonstrable (GA-2), establishing the data-plane's first redirect/live-path test story.

## Out of Scope

Explicitly excluded — owned by later features. Documented to prevent scope creep.

| Feature | Reason |
| --- | --- |
| The **M4 worker** that reads Postgres, builds the inactive slot's config maps, verifies them, and performs the **atomic `active_slot` swap** + rollback | M4 (*Agent worker* / *Double-buffer map build/swap*). This feature defines the map contract + the read/pin side; how maps get *authoritatively* populated from the DB and *atomically flipped* is M4's job (GA-1 decides the seed path used until then). |
| Ingress-cost cap, whitelist/VIP, blacklist (bloom+LPM), amplification/bogon filters, allow-rule matching, rate-limit & fairness buckets, `service_ceiling_drop`/`congestion_drop` | M3 (*Policy enforcement & fairness*). This feature's enabled-hit path goes **straight to redirect**; M3 inserts its stages between service-match and redirect, all reading the same pinned slot. |
| `ServicePlan` fields (committed/ceiling), VIP ceiling fields in the map | Consumed by M3 fairness/VIP, not the bare lookup. `service_map`'s value carries only what lookup+redirect need (service id + enabled flag). |
| Full §10.2 drop-reason set beyond `service_miss`/`service_disabled`; ringbuf/perf **sampling**; billing byte counters; bloom false-positive counters | *Drop-reason counters* (M2 #3) + M5. This feature adds only its own two reasons + reuses the minimal counter. |
| Global soft-bypass flag (`active_config` → pass-through everything) and per-node maintenance mode | M6 (*Bypass & maintenance mode*). `active_config` is introduced here for `active_slot`; the bypass/maintenance flags on it are M6's. |
| Telemetry aggregation / dashboards for redirect throughput, service hit rates | M5. This feature only bumps the raw `counter_map`. |
| IPv6 redirect, fragment forwarding | Hard-dropped upstream in parse (v1 scope). |

---

## User Stories

### P1: Service lookup & `service_miss` / `service_disabled` verdicts ⭐ MVP

**User Story**: As the gateway, I want to decide whether a valid IPv4 packet's destination is a declared,
enabled service, so that undeclared destinations and intentionally-disabled services are dropped with
distinct, attributable reasons and only real protected traffic proceeds to forwarding.

**Why P1**: This is the service-match node of §8.2 and the reason the pipeline exists past parse; it
replaces the packet-parse `XDP_PASS` service-lookup seam. `service_disabled` ≠ `service_miss` is a
product-committed distinction (AD-002/BL-03).

**Acceptance Criteria**:

1. WHEN a valid IPv4 packet exits parse THEN the system SHALL look its **destination IPv4** up in
   `service_map` using **longest-prefix match**, so a service declared on a CIDR matches every host
   address within that CIDR. `(SLRD-01)`
2. WHEN the destination matches **no** `service_map` entry THEN the system SHALL return `XDP_DROP` with
   reason `service_miss`. `(SLRD-02)`
3. WHEN the destination matches an entry whose `enabled` flag is **false** THEN the system SHALL return
   `XDP_DROP` with reason `service_disabled` — a **drop-all**, never a pass-through or a redirect
   (AD-002). `(SLRD-03)`
4. WHEN the destination matches an **enabled** entry THEN the system SHALL proceed to the redirect stage
   (P1 redirect story) carrying the matched service identity in `pkt_meta` for downstream (M3) reuse.
   `(SLRD-04)`
5. WHEN either service verdict is produced THEN the system SHALL record it in `counter_map` under a
   distinct `enum drop_reason` value (`DR_SERVICE_MISS`, `DR_SERVICE_DISABLED`), added without resizing
   the packet-parse `counter_map` (A-PKT-3 headroom). `(SLRD-05)`
6. WHEN a `service_map` lookup returns no value **and** the lookup itself failed (map read error, not a
   clean miss) THEN the system SHALL fail closed with `DR_MAP_ERROR` (fail-closed, §11.3), never treating
   a map error as a pass. `(SLRD-06)`

**Independent Test**: `BPF_PROG_TEST_RUN` with a seeded `service_map` — a frame to an enabled service's
IP → proceeds to redirect (retval `XDP_REDIRECT`); a frame to a disabled service's IP → `XDP_DROP` +
`service_disabled` counter; a frame to an unseeded IP → `XDP_DROP` + `service_miss` counter; a CIDR
service seeded on `/24` matches a host inside it.

---

### P1: Transparent `XDP_REDIRECT IN→OUT` (header-preserving) ⭐ MVP

**User Story**: As an inbound-only L2 transparent bridge, I want clean traffic to an enabled service
forwarded from `IN` to `OUT` without altering the packet, so that protected hosts receive traffic
unchanged and the box stays invisible at L3.

**Why P1**: This is the pipeline's clean-path terminal (§8.2 `XDP_REDIRECT IN→OUT`) and the header-
preservation is a hard product commitment (§6.2, §12.4). It replaces the packet-parse clean-IPv4
`XDP_PASS` placeholder.

**Acceptance Criteria**:

1. WHEN an enabled-service packet reaches the redirect stage THEN the system SHALL return `XDP_REDIRECT`
   targeting the `OUT` interface via a `tx_devmap` (devmap keyed to the `OUT` ifindex), forwarding at the
   XDP layer without traversing the Linux routing stack. `(SLRD-07)`
2. WHEN a packet is redirected THEN the system SHALL leave the IPv4 **TTL unchanged** and the IPv4 header
   **checksum unchanged** (no decrement, no incremental checksum) — a transparent L2 bridge, not a router
   (§6.2, §8.2 note). `(SLRD-08)`
3. WHEN a packet is redirected THEN the system SHALL preserve the full L2 header and any VLAN/QinQ tags
   **as received** (tags are not stripped or rewritten; the frame is forwarded verbatim). `(SLRD-09)`
4. WHEN the `tx_devmap` has no valid entry for `OUT` (misconfigured/unpopulated) THEN the redirect SHALL
   fail closed — the frame is dropped (`DR_MAP_ERROR`), never leaked to the host stack, and the condition
   is observable. `(SLRD-10)`
5. WHEN the `OUT` interface is provided to the loader THEN the loader SHALL resolve its ifindex, populate
   `tx_devmap`, and report it — distinct from the `IN` attach interface (extends the packet-parse loader,
   which took `IN` only, A-PKT-6). `(SLRD-11)`
6. WHEN redirect is chosen for any frame class (IPv4 service traffic and — per GA-3 — ARP) THEN the same
   single `tx_devmap`/redirect helper SHALL be used, so there is exactly one forwarding path to reason
   about. `(SLRD-12)`

**Independent Test**: `BPF_PROG_TEST_RUN` on an enabled-service frame returns `XDP_REDIRECT` with a
populated `tx_devmap`, and `XDP_DROP` (`map_error`) with an empty one. Real `IN→OUT` forwarding + TTL/
checksum-unchanged assertion is demonstrated per GA-2's chosen verification path.

---

### P1: `active_slot` snapshot/pin at ingress + config-map contract ⭐ MVP

**User Story**: As every current and future config-map lookup, I want each packet to see one consistent
configuration version, so that a worker flipping the active slot mid-stream never causes a packet to read
a hybrid of old and new config.

**Why P1**: The slot pin (§8.1/AD-005) is the invariant M3's every config lookup depends on; `service_map`
is the first slotted map and must establish the pattern correctly now. This story owns the machinery
GA-1 scopes.

**Acceptance Criteria**:

1. WHEN a packet begins the service stage THEN the system SHALL read `active_slot` from `active_config`
   **exactly once**, pin it into `pkt_meta`, and use that pinned slot for the `service_map` lookup (and
   every M3 config lookup thereafter). `(SLRD-13)`
2. WHEN `active_slot` is flipped between two packets THEN the earlier in-flight packet SHALL complete its
   lookups on its pinned slot and only subsequent packets SHALL see the new slot (no mid-packet hybrid
   view, §8.1). `(SLRD-14)`
3. WHEN `service_map` is defined THEN it SHALL be **slot-aware** (double-buffer-ready — two config
   versions selectable by `active_slot`) so the M4 worker can build an inactive slot and flip atomically
   without redefining the map or the hot-path lookup. `(SLRD-15)`
4. WHEN `active_config` is defined THEN it SHALL hold at least `active_slot` (0/1) and a config `version`,
   and `active_slot` SHALL be the single field the atomic swap writes (M4). `(SLRD-16)`
5. WHEN the maps must be populated **before the M4 worker exists** THEN the system SHALL provide the
   seed path chosen in GA-1 (e.g. a userspace loader/test helper that fills a slot and sets `active_slot`)
   so this feature is independently loadable, demoable, and testable. `(SLRD-17)`
6. WHEN a config-map key or `pkt_meta` slot field is built THEN it SHALL be zero-initialized with
   consistent padding before map use (§8.1), matching the packet-parse `pkt_meta` discipline. `(SLRD-18)`

**Independent Test**: Seed slot 0 with an enabled service and set `active_slot=0` → the frame redirects;
re-seed slot 1 with the service disabled, set `active_slot=1` → the same frame now drops `service_disabled`
— proving the lookup honors the pinned slot and the flip takes effect per-packet.

---

### P2: ARP transparent-bridge policy (resolves the packet-parse ARP seam)

**User Story**: As an inbound-only L2 transparent bridge, I want ARP handled per a definite bridge policy
now that the redirect path exists, so that L2 address resolution behaves correctly for protected hosts.

**Why P2**: ARP is not the volumetric threat surface and the P1 clean IPv4 path is demoable first; but the
packet-parse ARP seam (D-PKT-2, PKT-23/24) was explicitly deferred **to this feature** and must be closed.
The specific behavior depends on GA-3.

**Acceptance Criteria** *(the redirect branch assumes GA-3 = "redirect ARP"; if GA-3 keeps `XDP_PASS`,
criteria 1–2 collapse to "ARP continues to `XDP_PASS` and this story is documentation-only")*:

1. WHEN a frame's resolved EtherType is ARP THEN the system SHALL apply the GA-3 bridge policy — either
   `XDP_REDIRECT IN→OUT` via the same `tx_devmap` (transparent bridge) or the retained `XDP_PASS`. `(SLRD-19)`
2. WHEN ARP is redirected (GA-3 = redirect) THEN the frame SHALL be forwarded verbatim (no rewrite),
   consistent with the header-preservation rule (SLRD-09). `(SLRD-20)`
3. WHEN ARP is handled THEN it SHALL remain **not** counted as `unsupported_ethertype` and SHALL not be
   dropped (preserving packet-parse's non-destructive ARP guarantee, PKT-24). `(SLRD-21)`
4. WHEN the ARP policy is implemented THEN it SHALL reuse the single redirect helper (SLRD-12) and add no
   second forwarding path. `(SLRD-22)`

**Independent Test**: `BPF_PROG_TEST_RUN` on an ARP frame returns the GA-3 verdict (`XDP_REDIRECT` with a
populated `tx_devmap`, or `XDP_PASS`) and increments **no** drop-reason counter.

---

### P2: Redirect verification & header-preservation smoke test

**User Story**: As the platform engineer, I want the redirect decision covered by fast unit tests and the
real `IN→OUT` header-preserving forward demonstrable, so that both the verdict logic and the actual bridge
behavior are trusted before M3 builds on them.

**Why P2**: `BPF_PROG_TEST_RUN` proves the *decision* (`XDP_REDIRECT`/`service_miss`/`service_disabled`)
but cannot xmit a frame or prove TTL/checksum preservation; the real forward needs a live path. The split
(what's automated vs. manual/privileged) is GA-2.

**Acceptance Criteria**:

1. WHEN the unit suite runs THEN it SHALL assert, via `BPF_PROG_TEST_RUN`, the redirect verdict for
   enabled-service frames and the `service_miss`/`service_disabled`/`map_error` drops — with the seeded
   `service_map`/`tx_devmap`/`active_config`, requiring no NIC. `(SLRD-23)`
2. WHEN real forwarding is verified THEN the chosen GA-2 path SHALL forward a frame across a live
   `IN↔OUT` pair (e.g. two veths) and assert the received frame's **TTL and IPv4 checksum are identical**
   to the sent frame (header-preserving, SLRD-08). `(SLRD-24)`
3. WHEN the live-path test requires privileges/kernel features unavailable in the default unit environment
   THEN it SHALL be a separately-gated target (per the packet-parse `full`-gate convention), not part of
   the parallel-safe unit run. `(SLRD-25)`
4. WHEN the data-plane test conventions are updated THEN the new redirect/live-path patterns SHALL be
   added to `.specs/codebase/TESTING.md`'s data-plane section (extending the packet-parse A-PKT-2 section).
   `(SLRD-26)`

**Independent Test**: `make test` runs the `BPF_PROG_TEST_RUN` redirect/miss/disabled asserts green with
no NIC; the gated live target forwards a frame `IN→OUT` and shows TTL/checksum unchanged.

---

## Edge Cases

- WHEN a destination IP matches **two** overlapping `service_map` prefixes THEN LPM SHALL select the
  **most-specific** prefix (single service per address; the control-plane already forbids overlapping
  active service destinations globally — SRL-04/D-SRL-3 — so at most one active entry exists, but the
  data-plane still resolves deterministically).
- WHEN a service is disabled between two packets (slot flipped) THEN the first packet on the old pinned
  slot MAY redirect and the next on the new slot SHALL drop `service_disabled` — never a mid-packet mix
  (SLRD-14).
- WHEN `active_config` is unset/empty (no slot seeded yet) THEN every packet SHALL fail closed
  (`service_miss` or `map_error` per GA-1's seed contract), never redirect to an unconfigured `OUT`.
- WHEN `tx_devmap` points at an `OUT` interface whose driver lacks native XDP TX (`ndo_xdp_xmit`) THEN
  the redirect SHALL fail (dropped, observable) rather than silently pass — surfaced at load/verify time
  where possible (tie-in to the native-mandatory constraint, §8.1/§11.1).
- WHEN a non-TCP/UDP/ICMP IPv4 packet (GRE/ESP, ports 0 — valid per A-PKT-5) hits an enabled service THEN
  it SHALL redirect like any other enabled-service traffic (protocol admission is M3's rule engine, not
  the bare lookup).
- WHEN `service_map` is empty (zero services) THEN every valid IPv4 packet SHALL drop `service_miss`
  (correct fail-closed default before any config exists).

---

## Requirement Traceability

| Requirement ID | Story | PRD / TDD Ref | Phase | Status |
| --- | --- | --- | --- | --- |
| SLRD-01 | P1 Service lookup | §8.2, §4.3 `service_map` | Specify | Pending |
| SLRD-02 | P1 Service lookup | §8.2, §9.2 `service_miss` | Specify | Pending |
| SLRD-03 | P1 Service lookup | §8.2, §9.2 `service_disabled`, AD-002 | Specify | Pending |
| SLRD-04 | P1 Service lookup | §8.2 | Specify | Pending |
| SLRD-05 | P1 Service lookup | §10.2, A-PKT-3 | Specify | Pending |
| SLRD-06 | P1 Service lookup | §11.3 fail-closed | Specify | Pending |
| SLRD-07 | P1 Redirect | §8.2 `XDP_REDIRECT`, §4.3 `tx_devmap` | Specify | Pending |
| SLRD-08 | P1 Redirect | §6.2, §8.2 header-preserve | Specify | Pending |
| SLRD-09 | P1 Redirect | §6.2 transparent bridge | Specify | Pending |
| SLRD-10 | P1 Redirect | §11.3 fail-closed | Specify | Pending |
| SLRD-11 | P1 Redirect | A-PKT-6 loader | Specify | Pending |
| SLRD-12 | P1 Redirect | §8.2 | Specify | Pending |
| SLRD-13 | P1 Slot pin | §8.1, AD-005 | Specify | Pending |
| SLRD-14 | P1 Slot pin | §8.1, AD-005 | Specify | Pending |
| SLRD-15 | P1 Slot pin | §4.3 double-buffer, AD-005 | Specify | Pending |
| SLRD-16 | P1 Slot pin | §4.3 `active_config` | Specify | Pending |
| SLRD-17 | P1 Slot pin | GA-1 seed path | Specify | Pending |
| SLRD-18 | P1 Slot pin | §8.1 zero-init | Specify | Pending |
| SLRD-19 | P2 ARP | §8.2, D-PKT-2, GA-3 | Specify | Pending |
| SLRD-20 | P2 ARP | §6.2 | Specify | Pending |
| SLRD-21 | P2 ARP | PKT-24 | Specify | Pending |
| SLRD-22 | P2 ARP | §8.2 | Specify | Pending |
| SLRD-23 | P2 Verification | §12.4, D-PKT-4 | Specify | Pending |
| SLRD-24 | P2 Verification | §6.2, §12.4, GA-2 | Specify | Pending |
| SLRD-25 | P2 Verification | TESTING (full gate) | Specify | Pending |
| SLRD-26 | P2 Verification | TESTING, A-PKT-2 | Specify | Pending |

**ID format:** `SLRD-[NUMBER]`
**Status values:** Pending → In Design → In Tasks → Implementing → Verified
**Coverage:** 26 total, 0 mapped to tasks (Tasks phase pending) ⚠️

> IDs assigned in reading order within each story block (Service lookup 01–06, Redirect 07–12, Slot pin
> 13–18, ARP 19–22, Verification 23–26). Finalized in Design.

---

## Gray Areas — resolved in Discuss (2026-07-08)

All three took the recommended option; full rationale/trade-offs in `context.md` (D-SLRD-1..3).

- **GA-1 — Config-map ownership vs. the M4 gap → D-SLRD-1: own the read/pin side + a userspace seed
  helper.** This feature defines a slot-aware `service_map` + `active_config` + the ingress `active_slot`
  pin, seeded from a loader/test helper; the DB-driven build/verify/**atomic swap** stays M4. (Rejected:
  a single unslotted map deferring all slot/pin machinery to M4 — drops the ROADMAP scope line and
  re-touches the hot path later.)
- **GA-2 — Redirect verification without a NIC → D-SLRD-2: unit verdict + a gated live smoke.**
  `BPF_PROG_TEST_RUN` asserts the decision in the parallel-safe suite; a separately-gated live two-veth
  (`IN↔OUT`) smoke asserts real forwarding + TTL/checksum preservation (the first dp-integration test).
  (Rejected: a full privileged automated veth harness on every CI run.)
- **GA-3 — ARP bridge policy → D-SLRD-3: redirect ARP `IN→OUT`.** ARP switches from `XDP_PASS` to
  `XDP_REDIRECT IN→OUT` via the same `tx_devmap` (true transparent bridge; replies via the asymmetric/DSR
  path, CM-09), never mis-counted or dropped. (Rejected: keeping ARP at `XDP_PASS` — leaves the bridge
  not-fully-transparent and only half-closes the seam.)

---

## Success Criteria

How we know the feature is successful:

- [ ] A valid IPv4 frame to an **enabled** service's IP is `XDP_REDIRECT`-ed `IN→OUT`; to a **disabled**
      service drops `service_disabled`; to an **undeclared** IP drops `service_miss` — each verified by
      `BPF_PROG_TEST_RUN` with a seeded slot.
- [ ] A CIDR-declared service matches every host inside it (LPM), and overlapping prefixes resolve to the
      most-specific.
- [ ] A redirected frame arrives on `OUT` with **TTL and IPv4 checksum identical** to ingress (header-
      preserving), demonstrated by the GA-2 live path.
- [ ] Flipping `active_slot` between packets changes the verdict on the new-slot packet only — no
      mid-packet hybrid view (slot pin holds).
- [ ] The ARP seam is closed per GA-3, with ARP never mis-counted as `unsupported_ethertype`.
- [ ] `map_error`/unpopulated-`tx_devmap`/empty-`active_config` all **fail closed** (drop), never leak to
      the host stack or an unconfigured `OUT`.
- [ ] Data-plane redirect/live-path test conventions added to `.specs/codebase/TESTING.md`; the program
      still passes the verifier and loads native on `IN`.
</content>
</invoke>
