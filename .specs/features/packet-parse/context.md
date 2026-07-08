# Packet Parse & Fail-Fast — Context (Discuss output)

**Spec:** `.specs/features/packet-parse/spec.md`
**Captured:** 2026-07-08 (discuss within Specify)
**Status:** Ready for design

---

## Feature Boundary

The **head of the §8.2 data-plane pipeline** and the **first C/XDP/eBPF code** in a repo that is
otherwise Python control-plane. It owns everything from *frame arrives on `IN`* up to *a valid IPv4
packet is ready for service lookup*, and it bootstraps the data-plane project itself.

It sits on two seams:

- **Downstream (this feature ends here):** *Service lookup & transparent redirect* (M2 #2) owns
  `service_map` match, `service_miss`/`service_disabled`, `active_slot` snapshot/pin, `tx_devmap`, and
  `XDP_REDIRECT IN→OUT`. This feature hands off at a **marked `XDP_PASS` seam** (D-PKT-3) with `pkt_meta`
  fully populated; the ARP branch is a second marked seam for redirect (D-PKT-2).
- **Sideways:** *Drop-reason counters* (M2 #3) owns the **full** §10.2 reason set across every stage,
  ringbuf/perf **sampling**, and bloom false-positive counters. This feature ships only the shared
  `enum drop_reason` and a **minimal per-CPU counter** so its own verdicts are test-observable (A-PKT-3).

So this feature owns: the **`data-plane/` scaffold** (build + native-mode loader + `BPF_PROG_TEST_RUN`
harness), the **single-parse `pkt_meta` contract**, the **L2/VLAN/QinQ EtherType resolution**, the
**four fail-fast drops**, the **ARP classify + `XDP_PASS`** policy, and the **data-plane test conventions**
it adds to `TESTING.md`. It loads a **static** program — no config maps, no worker, no double-buffer yet.

---

## Implementation Decisions

Four gray areas were resolved with the user before writing the spec (this feature enters a new domain,
so the decisions are as much *scoping* as *behavioral*).

### D-PKT-1: This feature bootstraps the data-plane scaffold (build + native-mode loader + test harness)

**Question:** This is the first C/XDP code — does this feature also create the build system, the XDP
loader, and the test harness, or should those be a separate skeleton feature / assumed to exist?
**Decision:** **Bootstrap the minimal scaffold here.** This feature creates `data-plane/` with a
`clang -target bpf` + libbpf-skeleton build, a userspace **loader that attaches to `IN` in native/DRV
mode** (`XDP_FLAGS_DRV_MODE`), and a `BPF_PROG_TEST_RUN` test harness — with parse + fail-fast as the
functional payload. Native attach failure is a **loud error, no silent generic fallback** (§8.1/§11.1);
generic-mode **alerting** is deferred to M6.
**Why:** No downstream data-plane feature can compile, load, or be tested without this loop; folding it
in mirrors how *auth-rbac* bootstrapped the control-plane skeleton, and avoids a near-empty standalone
skeleton feature. Fail-loud-on-non-native keeps the mandatory-native constraint honest from day one.
**Trade-off:** This is a **Large/Complex** feature (scaffold + parse), not a small one; the loader and
build system are one-time costs paid inside a feature whose "headline" is parsing.
**Impact:** `PKT-01..06`. Creates `data-plane/`; adds a data-plane section to `.specs/codebase/TESTING.md`
(A-PKT-2). No change to the control-plane.

### D-PKT-2: ARP is classified and `XDP_PASS`-ed (minimal policy), with a marked redirect seam

**Question:** It's an inbound-only L2 transparent bridge and the redirect machinery (`tx_devmap`,
`XDP_REDIRECT`) belongs to the next feature. What is v1's minimal ARP policy *at the parse stage*?
**Decision:** **Classify ARP and `XDP_PASS`** to the host stack (non-destructive), leaving a
clearly-marked seam so the ARP branch can be switched to `XDP_REDIRECT IN→OUT` when the redirect feature
lands. ARP is **not** counted as `unsupported_ethertype`.
**Why:** This feature cannot correctly bridge ARP `IN→OUT` yet (no `tx_devmap`), and dropping ARP would
break L2 resolution. `XDP_PASS` is the safe interim that keeps the door open without stealing the next
feature's redirect ownership.
**Trade-off:** ARP does not actually traverse `IN→OUT` until the redirect feature; in a pure transparent
bridge with no host IP on `IN`, passed ARP may go nowhere useful until then — acceptable for a
parse-stage milestone. Revisit the seam when redirect ships.
**Impact:** `PKT-23..24`. The §8.2 diagram's "Pass/redirect ARP theo chính sách bridge tối thiểu" branch
is realized as *pass* in v1-M2#1, *redirect* in v1-M2#2.

### D-PKT-3: Valid IPv4 exits at a marked `XDP_PASS` service-lookup seam

**Question:** Service lookup/redirect isn't built yet — when parse succeeds on a clean IPv4 packet, how
does it leave this feature? (This drives how "OK IPv4 continues" is tested.)
**Decision:** **`XDP_PASS` placeholder + marked seam.** Parseable IPv4 returns `XDP_PASS` at a single,
clearly-marked hand-off point with `pkt_meta` fully populated. The next feature replaces the placeholder
with the service-lookup call; tests here assert `XDP_PASS` **and** the `pkt_meta` field values.
**Why:** Keeps this feature independently demoable (clean traffic visibly passes; unsupported visibly
drops) and testable without any downstream code, while the single seam makes the next feature a drop-in.
Chosen over a tail-call stub (more infra now for marginal gain) and over fail-closed-drop (can't demo
clean-traffic pass-through, and would masquerade as `service_miss` which this feature doesn't own).
**Trade-off:** During M2 #1 only, clean IPv4 is passed to the kernel rather than redirected — a temporary
state removed by the redirect feature; not a shippable end state on its own (the pipeline is incomplete
until M2 #2/#3 + M3).
**Impact:** `PKT-15`. The seam is the integration contract with *Service lookup & transparent redirect*.

### D-PKT-4: Data-plane verified via `BPF_PROG_TEST_RUN` synthetic packets (establishes the DP TESTING pattern)

**Question:** `TESTING.md` defers data-plane test conventions to this milestone — how do we verify parse
& fail-fast?
**Decision:** **`BPF_PROG_TEST_RUN` with synthetic packet bytes.** Unit-test the **actual loaded** XDP
program by feeding hand-crafted frames (IPv4/IPv6/ARP/malformed/fragment/VLAN/QinQ/truncated-L4) and
asserting the returned XDP action + the minimal drop-reason counter. No NIC, no root-only veth needed for
the core suite. This becomes the data-plane convention in `TESTING.md`.
**Why:** Exercises the **real verifier-approved program** (a host-compiled parser mirror would test a
copy, not the loaded object; live-veth is highest fidelity but needs `CAP_NET_ADMIN`/root CI and is
slow). `BPF_PROG_TEST_RUN` is the standard XDP unit-test mechanism and gives per-verdict assertions.
**Trade-off:** `BPF_PROG_TEST_RUN` requires a kernel with BPF test-run support in CI (privileges to load
BPF); it does not exercise the NIC driver's native-XDP path (a small live-veth smoke test is a candidate
for a later gate). Reasoning verified against standard libbpf/XDP practice; kernel/CI version to be
pinned in Design.
**Impact:** `PKT-04, PKT-06`; new data-plane section in `.specs/codebase/TESTING.md` (A-PKT-2).

---

## Assumptions (flagged — confirm or override in Design)

- **A-PKT-1 (VLAN/QinQ depth):** Supported tag depth is **2** (single 802.1Q + QinQ 802.1ad→802.1Q).
  More than 2 stacked tags → `unsupported_ethertype`. Traversal is a **bounded** loop (verifier-friendly),
  and tags are **preserved** (transparent bridge, not stripped). `PKT-19..22`.
- **A-PKT-2 (TESTING.md):** This feature adds a **data-plane section** to `.specs/codebase/TESTING.md`
  (build/quick/full gate commands for C/XDP, `BPF_PROG_TEST_RUN` fixtures, synthetic-packet builders),
  paralleling AD-008 for the control-plane. Data-plane unit tests are parallel-safe; a future live-veth
  gate would not be.
- **A-PKT-3 (minimal counter seam):** This feature defines the shared `enum drop_reason` (the 4 fail-fast
  reasons + `map_error`) and a **minimal per-CPU `counter_map`** purely to make verdicts test-observable.
  The **full** §10.2 reason coverage, ringbuf/perf **sampling**, and bloom false-positive counters remain
  the *Drop-reason counters* feature (M2 #3). If that feature prefers to own the enum too, this reduces
  to a test-only debug map.
- **A-PKT-4 (L4 truncation = malformed):** A present-but-truncated TCP/UDP/ICMP header is classified
  `malformed_ipv4` (no separate `malformed_l4` reason in v1, since §10.2 defines none). `PKT-14`.
- **A-PKT-5 (non-TCP/UDP/ICMP IPv4 continues):** Other IPv4 protocols (GRE/ESP/…) are **valid** and
  continue to the seam with L4 ports zeroed; protocol admission is the M3 rule engine's call, not a
  parse-stage drop. `PKT-14`, Edge Cases.
- **A-PKT-6 (`IN` interface is loader config):** The `IN` interface name is a loader argument/env value;
  the `OUT` interface (redirect target) is not referenced by this feature. No hardcoded ifname.
- **A-PKT-7 (fragment definition):** Fragment = IPv4 `MF` flag set **or** `frag_offset != 0`; both first
  and subsequent fragments drop `fragment_unsupported`. No reassembly, ever, in v1. `PKT-09`.

---

## Cross-feature effects

- **Consumed by:** *Service lookup & transparent redirect* (reads `pkt_meta`, replaces the `XDP_PASS`
  seam and the ARP seam) and all of M3 (whitelist/blacklist/rules/buckets read `pkt_meta`).
- **Establishes:** the `data-plane/` project, the `enum drop_reason` contract, and the data-plane test
  conventions in `TESTING.md` — all reused by every subsequent data-plane feature.
- **No control-plane change.** No DB, no Redis, no API surface. Independent of the M1 apply-status work.
