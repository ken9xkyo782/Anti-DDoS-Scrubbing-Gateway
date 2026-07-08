# Packet Parse & Fail-Fast Specification

**Milestone:** M2 — Data-plane verdict pipeline (XDP core)
**Feature #1 of M2** (head of the §8.2 pipeline)
**Status:** Spec drafted — awaiting approval → Design
**Context (gray areas):** `.specs/features/packet-parse/context.md` (D-PKT-1..4, A-PKT-1..7)

## Problem Statement

The data-plane does not exist yet — the repo is Python control-plane only. The §8.2 verdict
pipeline needs a head: a **native-XDP** program on interface `IN` that parses each frame **exactly
once** into a `pkt_meta` contract and **fails fast** (fail-closed, PRD §11.3) on any traffic v1 does
not support — IPv6, unknown EtherTypes, malformed IPv4, and fragments — before any expensive
service/rule/list lookup runs. Every downstream stage (service lookup, whitelist, blacklist, rules,
buckets) reads `pkt_meta`; getting the single-parse contract and the fail-fast verdicts right here is
the foundation the rest of M2/M3 builds on.

## Goals

- [ ] A native-XDP program attaches to `IN` in **DRV mode** and returns a verdict for every frame.
- [ ] The four fail-fast drops of §8.2/§10.2 (`ipv6_unsupported`, `unsupported_ethertype`,
      `malformed_ipv4`, `fragment_unsupported`) are produced with the correct standardized reason.
- [ ] A single parse populates a `pkt_meta` struct consumed by all downstream stages (§8.1 "parse once").
- [ ] Every packet-data access is bounds-checked against `data_end` — the verifier accepts the program
      and truncated input is failed closed, never read out of bounds (§11.3).
- [ ] Clean IPv4 exits at a **marked service-lookup seam** (`XDP_PASS` placeholder) with `pkt_meta`
      fully populated and assertable.
- [ ] Data-plane test conventions established: `BPF_PROG_TEST_RUN` synthetic-packet unit tests, one
      assertion per verdict, no NIC required (extends `.specs/codebase/TESTING.md`).

## Out of Scope

Explicitly excluded — owned by later features. Documented to prevent scope creep.

| Feature | Reason |
| --- | --- |
| Service match, `service_miss`/`service_disabled`, `XDP_REDIRECT IN→OUT`, `tx_devmap`, `active_slot` pin | Next M2 feature (*Service lookup & transparent redirect*). This feature ends at the seam. |
| Whitelist/VIP, blacklist (bloom+LPM), amplification/bogon filters, allow-rules, rate-limit & fairness buckets | M3 (*Policy enforcement & fairness*). All read `pkt_meta` produced here. |
| Full §10.2 drop-reason set beyond the 4 fail-fast reasons; ringbuf/perf **sampling**; bloom false-positive counters; dashboards | *Drop-reason counters* (M2 #3) + M5 observability. This feature ships only the shared `enum drop_reason` + a minimal per-CPU counter for test observability (A-PKT-3). |
| ARP **redirect** `IN→OUT` | Needs `tx_devmap`/redirect (next feature). Here ARP is classified and `XDP_PASS`-ed (D-PKT-2). |
| IPv6 forwarding, IPv4 fragment reassembly | Hard-dropped in v1 by product scope (PROJECT/§4.2). |
| Generic-mode (SKB) fallback **alerting**, XDP native→generic event | M6 alerting. Loader here fails loudly on non-native attach (D-PKT-1) but raises no alert. |
| Worker, BPF map build/verify/swap, double-buffer | M4. This feature loads a static program; no config maps yet. |

---

## User Stories

### P1: Native-XDP data-plane scaffold & loader ⭐ MVP

**User Story**: As the platform engineer, I want a buildable native-XDP program attached to `IN` with a
test harness, so that all subsequent data-plane features have a compile → load → verify → test loop.

**Why P1**: First C/XDP code in the repo. Nothing downstream can exist without the build system, the
native-mode loader, and the `BPF_PROG_TEST_RUN` harness. Mirrors how *auth-rbac* bootstrapped the
control-plane skeleton.

**Acceptance Criteria**:

1. WHEN `make` (or the documented build) runs THEN the toolchain SHALL compile the XDP source to a BPF
   object (`clang -target bpf`) and generate a libbpf skeleton, with no verifier-invalidating constructs.
2. WHEN the loader is run against an interface name THEN it SHALL attach the XDP program to that
   interface in **native/DRV mode** (`XDP_FLAGS_DRV_MODE`).
3. WHEN native attach fails (driver lacks XDP) THEN the loader SHALL exit with a clear error and **not**
   silently fall back to generic/SKB mode (native is mandatory, §8.1/§11.1; alerting deferred to M6).
4. WHEN the loader reports status THEN it SHALL surface the **actual attach mode** it achieved.
5. WHEN the loader exits or is signalled THEN it SHALL detach the program and leave the interface clean.
6. WHEN the test harness runs THEN it SHALL execute the loaded program against in-memory synthetic
   packets via `BPF_PROG_TEST_RUN` and assert on the returned XDP action, requiring no live NIC.

**Independent Test**: `make` builds the object + skeleton; the harness loads it and runs a trivial
pass-through packet returning `XDP_PASS`; loader attach/detach demonstrated on a test veth or reported
error on a non-XDP driver.

---

### P1: Fail-fast drop of unsupported traffic ⭐ MVP

**User Story**: As the gateway, I want to drop clearly-unsupported traffic at the earliest point with a
standardized reason, so that malicious/irrelevant volumetric traffic never reaches expensive lookups
and every drop is attributable.

**Why P1**: This is the feature's core purpose and the §12.4 acceptance surface (fail-closed §11.3).

**Acceptance Criteria**:

1. WHEN a frame's resolved EtherType is IPv6 THEN system SHALL return `XDP_DROP` with reason
   `ipv6_unsupported`.
2. WHEN a frame's resolved EtherType is neither IPv4 nor ARP (nor a VLAN tag it can unwrap) THEN system
   SHALL return `XDP_DROP` with reason `unsupported_ethertype`.
3. WHEN an IPv4 header is malformed — `version != 4`, `ihl < 5`, header/`total_len` truncated beyond
   `data_end`, or internally inconsistent — THEN system SHALL return `XDP_DROP` with reason
   `malformed_ipv4`.
4. WHEN an IPv4 packet is a fragment — `MF` flag set **or** `frag_offset != 0` — THEN system SHALL
   return `XDP_DROP` with reason `fragment_unsupported` (no reassembly).
5. WHEN any L2/L3/L4 field read would exceed `data_end` THEN system SHALL fail closed (drop with the
   appropriate `malformed_ipv4`/`unsupported_ethertype` reason), never reading out of bounds.
6. WHEN any verdict is reached THEN system SHALL define and use a shared `enum drop_reason` and record
   the reason in a minimal per-CPU counter, so each verdict is observable to tests (A-PKT-3).

**Independent Test**: `BPF_PROG_TEST_RUN` fed a crafted IPv6 / non-IP / malformed-IPv4 / fragmented
frame each returns `XDP_DROP` and increments the matching drop-reason counter.

---

### P1: Single-parse `pkt_meta` contract for clean IPv4 + L4 ⭐ MVP

**User Story**: As every downstream pipeline stage, I want one authoritative parse result, so that no
stage re-parses the packet and all stages see a consistent view (§8.1 "parse once").

**Why P1**: `pkt_meta` is the contract the entire rest of the pipeline consumes; its shape is a
cross-feature commitment that must be fixed now.

**Acceptance Criteria**:

1. WHEN a supported IPv4 frame is parsed THEN system SHALL populate a single `pkt_meta` with at least:
   resolved EtherType, source/destination IPv4, IP protocol, IHL / L4 offset, and — for TCP/UDP —
   source & destination ports (for ICMP — type & code).
2. WHEN the IPv4 protocol is TCP or UDP THEN system SHALL extract L4 ports; WHEN it is ICMP THEN type &
   code; WHEN it is any other IPv4 protocol THEN `pkt_meta` SHALL carry the protocol with ports zeroed
   and the packet SHALL remain valid (continues to the seam — protocol filtering is the rule engine's job).
2b. WHEN an L4 header is present but truncated beyond `data_end` for its declared protocol THEN system
   SHALL return `XDP_DROP` with reason `malformed_ipv4`.
3. WHEN parse succeeds THEN system SHALL return `XDP_PASS` at a **clearly-marked service-lookup seam**
   (a single hand-off point the next feature replaces) — no drop, no redirect.
4. WHEN a downstream stage needs a packet field THEN it SHALL read it from `pkt_meta`, never re-parse
   the frame (single-parse invariant, §8.1).
5. WHEN the parse path runs THEN it SHALL hold **no per-source-IP state** — parse is stateless per packet
   (§11.1).
6. WHEN a struct key/`pkt_meta` is built THEN it SHALL be zero-initialized with consistent padding
   before any map use (§8.1).

**Independent Test**: `BPF_PROG_TEST_RUN` fed a TCP, a UDP, and an ICMP IPv4 frame returns `XDP_PASS`
and the exposed `pkt_meta` (via a test/debug map or `data_meta`) carries the expected src/dst/proto/ports.

---

### P2: VLAN / QinQ EtherType resolution

**User Story**: As the gateway on a trunked link, I want to see through 802.1Q / QinQ tags to the real
EtherType, so that tagged IPv4 is protected and tagged unsupported traffic is still failed fast.

**Why P2**: Real deployments use VLANs, but the core fail-fast/parse contract (P1) is demoable on
untagged frames first.

**Acceptance Criteria**:

1. WHEN a frame carries a single 802.1Q tag THEN system SHALL unwrap it to the inner EtherType and
   continue parsing, preserving the tag (transparent bridge — tags are not stripped).
2. WHEN a frame carries a double tag (802.1ad outer + 802.1Q inner, QinQ) THEN system SHALL unwrap both
   to the inner EtherType.
3. WHEN a frame stacks more than the supported tag depth (2) or a tag is truncated THEN system SHALL
   return `XDP_DROP` with reason `unsupported_ethertype` (bounded, verifier-friendly traversal).
4. WHEN the inner EtherType is resolved THEN all P1 EtherType branching (IPv4/IPv6/ARP/unsupported)
   SHALL apply identically to the tagged case.

**Independent Test**: `BPF_PROG_TEST_RUN` fed a VLAN-tagged IPv4 frame → `XDP_PASS` with correct
`pkt_meta`; a QinQ IPv4 frame → `XDP_PASS`; a triple-tagged frame → `XDP_DROP unsupported_ethertype`.

---

### P2: ARP minimal bridge policy (classify + `XDP_PASS`)

**User Story**: As an inbound-only L2 transparent bridge, I want ARP handled non-destructively, so that
L2 address resolution is not broken while the redirect path is still being built.

**Why P2**: ARP is not the volumetric threat surface; it needs a safe, minimal policy, not the full
bridge redirect (which the next feature owns).

**Acceptance Criteria**:

1. WHEN a frame's resolved EtherType is ARP THEN system SHALL classify it and return `XDP_PASS` (hand
   to host stack) — **not** a drop, and **not** counted as `unsupported_ethertype`.
2. WHEN the redirect feature lands THEN the ARP branch SHALL be a clearly-marked seam so ARP can be
   switched to `XDP_REDIRECT IN→OUT` without touching the parse logic (D-PKT-2).

**Independent Test**: `BPF_PROG_TEST_RUN` fed an ARP frame returns `XDP_PASS` and increments no
drop-reason counter.

---

## Edge Cases

- WHEN an Ethernet frame is shorter than a full L2 header THEN system SHALL drop `unsupported_ethertype`
  (cannot resolve EtherType), failing closed.
- WHEN IPv4 `total_len` claims more bytes than the frame provides THEN system SHALL drop `malformed_ipv4`.
- WHEN IPv4 `ihl` indicates options but the option bytes are truncated THEN system SHALL drop
  `malformed_ipv4`.
- WHEN a UDP/TCP header is truncated after a valid IPv4 header THEN system SHALL drop `malformed_ipv4`
  (A-PKT-4 — L4 truncation classified as malformed, not a separate reason in v1).
- WHEN an IPv4 first fragment (offset 0, `MF=1`) arrives THEN system SHALL drop `fragment_unsupported`
  (same as later fragments — no reassembly).
- WHEN EtherType `0x0000` / jumbo / runt frames arrive THEN system SHALL resolve to unsupported and drop
  `unsupported_ethertype` without crashing the verifier.
- WHEN a non-TCP/UDP/ICMP IPv4 protocol (e.g. GRE, ESP) arrives THEN system SHALL continue to the seam
  with ports zeroed (not drop) — protocol admission is the M3 rule engine's decision.

---

## Requirement Traceability

| Requirement ID | Story | PRD Ref | Phase | Status |
| --- | --- | --- | --- | --- |
| PKT-01 | P1 Scaffold | §8.1, §11.1 | Design | Pending |
| PKT-02 | P1 Scaffold | §8.1, §11.1 | Design | Pending |
| PKT-03 | P1 Scaffold | §8.1, §11.1, §11.3 | Design | Pending |
| PKT-04 | P1 Scaffold | §11.1 | Design | Pending |
| PKT-05 | P1 Scaffold | §11.3 | Design | Pending |
| PKT-06 | P1 Scaffold (test harness) | §12.4, TESTING | Design | Pending |
| PKT-07 | P1 Fail-fast | §8.2, §10.2, §12.4 | Design | Pending |
| PKT-08 | P1 Fail-fast | §8.2, §10.2, §12.4 | Design | Pending |
| PKT-09 | P1 Fail-fast | §8.2, §10.2, §12.4 | Design | Pending |
| PKT-10 | P1 Fail-fast | §8.2, §10.2, §12.4 | Design | Pending |
| PKT-11 | P1 Fail-fast | §8.1, §11.3 | Design | Pending |
| PKT-12 | P1 Fail-fast (enum + counter) | §10.2 | Design | Pending |
| PKT-13 | P1 pkt_meta | §8.1 | Design | Pending |
| PKT-14 | P1 pkt_meta | §8.1, §8.2 | Design | Pending |
| PKT-15 | P1 pkt_meta | §8.2 | Design | Pending |
| PKT-16 | P1 pkt_meta | §8.2, §8.4 | Design | Pending |
| PKT-17 | P1 pkt_meta | §8.1 | Design | Pending |
| PKT-18 | P1 pkt_meta | §11.1 | Design | Pending |
| PKT-19 | P2 VLAN/QinQ | §8.2 | Design | Pending |
| PKT-20 | P2 VLAN/QinQ | §8.2 | Design | Pending |
| PKT-21 | P2 VLAN/QinQ | §8.2, §10.2 | Design | Pending |
| PKT-22 | P2 VLAN/QinQ | §8.2 | Design | Pending |
| PKT-23 | P2 ARP | §8.2 | Design | Pending |
| PKT-24 | P2 ARP | §8.2 | Design | Pending |

**ID format:** `PKT-[NUMBER]`
**Status values:** Pending → In Design → In Tasks → Implementing → Verified
**Coverage:** 24 total, 0 mapped to tasks (Tasks phase pending) ⚠️

> Requirement text is enumerated per story above; IDs are assigned in reading order within each story
> block (Scaffold PKT-01..06, Fail-fast PKT-07..12, pkt_meta PKT-13..18, VLAN/QinQ PKT-19..22, ARP
> PKT-23..24) and finalized in Design.

---

## Success Criteria

How we know the feature is successful:

- [ ] `make` builds the XDP object + libbpf skeleton clean; the program passes the kernel verifier when
      loaded.
- [ ] Loader attaches to `IN` in native/DRV mode, reports the mode, detaches cleanly, and errors (not
      silently degrades) when native is unavailable.
- [ ] All four §10.2 fail-fast reasons are produced correctly for their inputs, verified by
      `BPF_PROG_TEST_RUN` (§12.4 packet-verdict cases pass).
- [ ] A clean IPv4 TCP/UDP/ICMP frame yields `XDP_PASS` with a `pkt_meta` whose fields match the input.
- [ ] No verifier rejection, no out-of-bounds read across the full synthetic-packet corpus (including
      truncated/runt/jumbo/deep-VLAN adversarial frames).
- [ ] Data-plane testing conventions (`BPF_PROG_TEST_RUN`, gate commands) added to
      `.specs/codebase/TESTING.md` for reuse by the next M2/M3 features.
