# Service Lookup & Transparent Redirect — Context (Discuss output)

**Spec:** `.specs/features/service-lookup-redirect/spec.md` (SLRD-01..26)
**Captured:** 2026-07-08 (discuss within Specify)
**Status:** Ready for design

---

## Feature Boundary

The **clean-path terminal of the §8.2 pipeline** and the first feature to introduce **config maps** and
the **per-packet slot pin**. It owns everything from *a valid IPv4 packet exits parse* up to *the frame
is redirected `IN→OUT` or dropped with a service verdict*. It replaces the two marked seams that
*Packet parse & fail-fast* left: the `XDP_PASS` service-lookup seam (PKT-15) and the ARP `XDP_PASS` seam
(PKT-23/24).

It sits on three boundaries:

- **Upstream (consumes):** packet-parse's stack `pkt_meta`, `enum drop_reason` + `record_drop()` +
  `counter_map`, native-mode loader, and `BPF_PROG_TEST_RUN` harness.
- **Downstream (M3 inserts here):** *Policy enforcement & fairness* slots ingress-cap → whitelist/VIP →
  blacklist → allow-rules → fairness buckets **between** service-match and redirect, all reading the same
  pinned slot this feature establishes. This feature's enabled-hit path goes **straight to redirect** for now.
- **Sideways (M4 completes):** *Agent worker* / *Double-buffer map build/swap* provides the authoritative
  Postgres→map build, verification, and **atomic `active_slot` swap** + rollback. This feature defines the
  map contract + the read/pin side and seeds it from userspace until M4 lands.

So this feature owns: the **slot-aware `service_map`** (LPM by dst IPv4) + **`active_config`** + the
**ingress `active_slot` snapshot/pin**, the **two service verdicts** (`service_miss`/`service_disabled`),
the **transparent `XDP_REDIRECT IN→OUT`** (`tx_devmap`, header-preserving), the **ARP redirect** policy,
a **userspace seed path**, and the **data-plane redirect/live-path test conventions**.

---

## Implementation Decisions

Three gray areas were resolved with the user before finalizing the spec (this feature sets cross-feature
map/testing/bridge contracts, so the decisions are as much *contract* as *behavioral*). All three took
the recommended option.

### D-SLRD-1: This feature owns the config-map **read/pin side** + a userspace seed helper; M4 owns the DB build + atomic swap

**Question:** The M4 worker that builds `service_map` from Postgres and flips `active_slot` atomically
does not exist yet. How much of the config-map/slot machinery does this feature own — the full read/pin
side + a seed path, or a single unslotted map with everything slot-related deferred to M4?
**Decision:** **Own the read/pin side now.** This feature defines a **slot-aware `service_map`** (LPM by
destination IPv4, double-buffer-ready — two config versions selectable by `active_slot`) and an
**`active_config`** map holding `active_slot` (0/1) + `version`; implements the **ingress snapshot/pin**
of `active_slot` into `pkt_meta` (read once per packet, used for every lookup); and provides a
**userspace loader/test seed helper** that fills a slot and sets `active_slot` so the feature is
independently loadable, demoable, and testable. The **authoritative Postgres→map build, verification, and
atomic `active_slot` swap + rollback stay M4** — this feature ships no worker, no Redis, no DB read.
**Why:** The ROADMAP assigns "`active_slot` snapshot/pin at ingress (consistent per-packet view)" to this
feature; the pin is the invariant all of M3's config lookups depend on, so it must be established
correctly now, not retrofitted. Owning the read side + a stable map contract lets M4 drop in the writer
without redefining the map or touching the hot path. Mirrors how packet-parse bootstrapped scaffold it
didn't fully "consume" yet.
**Trade-off:** This feature builds slot machinery whose *only* writer (until M4) is a test/loader seed
helper — the double-buffer's real value (atomic hot swap) isn't exercised until M4. The seed path is a
throwaway relative to the worker (but the map/pin contract it populates is permanent).
**Impact:** `SLRD-13..18`. Introduces `service_map` + `active_config` (slot-aware, config group per
§4.3/AD-005) and `tx_devmap` (runtime). No control-plane change. Hands M4 the maps to fill.

### D-SLRD-2: Redirect verified by **unit verdict (`BPF_PROG_TEST_RUN`) + a gated live two-veth smoke**

**Question:** `BPF_PROG_TEST_RUN` proves the redirect *verdict* but cannot xmit a frame or prove
TTL/checksum preservation. Unit-decision + gated live smoke, or a full automated privileged veth harness
in CI now?
**Decision:** **Unit-test the decision + add a gated live smoke.** The parallel-safe unit suite asserts,
via `BPF_PROG_TEST_RUN` with a seeded `service_map`/`tx_devmap`/`active_config`, the redirect verdict for
enabled-service frames and the `service_miss`/`service_disabled`/`map_error` drops (no NIC). A
**separately-gated live two-veth (`IN↔OUT`) smoke test** (a `full`-gate/manual target) actually forwards a
frame and asserts the received frame's **TTL and IPv4 checksum are identical** to the sent frame
(header-preserving). This is the first data-plane **dp-integration** test.
**Why:** `BPF_PROG_TEST_RUN` exercises the real verifier-approved program's *decision* but a redirect's
actual xmit + header preservation is only observable on a live path. Splitting keeps the everyday suite
fast, NIC-free, and parallel-safe (packet-parse's convention) while still proving real forwarding before
M3 builds on it. Realizes packet-parse Open Question #3 (live-veth smoke) for the redirect case.
**Trade-off:** The live smoke needs `CAP_NET_ADMIN`/root + a BPF-capable kernel and is **not**
parallel-safe → it runs in the `full` gate / manually, not on every unit run; header preservation is thus
proven in the gated path, not the quick path.
**Impact:** `SLRD-23..26`. Extends the data-plane section of `.specs/codebase/TESTING.md` (A-PKT-2) with
the redirect/live-path conventions (dp-integration gate, veth fixture, TTL/csum assertion).

### D-SLRD-3: ARP switches from `XDP_PASS` to `XDP_REDIRECT IN→OUT` (true transparent bridge)

**Question:** The packet-parse ARP seam (D-PKT-2) is this feature's to close now that the redirect path
exists. Redirect ARP `IN→OUT`, or keep it at `XDP_PASS`?
**Decision:** **Redirect ARP `IN→OUT`** via the same `tx_devmap`/redirect helper, forwarded verbatim (no
rewrite), consistent with the header-preservation rule (SLRD-09). ARP remains **not** counted as
`unsupported_ethertype` and is never dropped (preserves PKT-24's non-destructive guarantee). ARP replies
return via the asymmetric/DSR path (CM-09), not this XDP program.
**Why:** The box is an inbound-only **L2 transparent bridge** with no useful IP on `IN`; passing ARP to
its host stack goes nowhere useful. Redirecting ARP `IN→OUT` lets upstream resolve the protected hosts'
MACs — the transparent-bridge-correct behavior. PRD §8.2 explicitly leaves this open
("pass/redirect ARP per minimal bridge policy"); the redirect path now exists to do it right. Uses the
single redirect helper (SLRD-12/22) → no second forwarding path.
**Trade-off:** ARP now depends on the `OUT` driver's `ndo_xdp_xmit` (native XDP TX) and on the asymmetric
return path (CM-09) for replies; a misconfigured `tx_devmap` fails ARP closed (dropped) like service
traffic. Acceptable — CM-09 is an already-accepted network prerequisite.
**Impact:** `SLRD-19..22`. Realizes the §8.2 diagram's "Pass/redirect ARP" branch as **redirect** in
v1-M2#2 (packet-parse realized it as *pass* in v1-M2#1).

---

## Assumptions (flagged — confirm or override in Design)

- **A-SLRD-1 (`service_map` shape & value):** `service_map` is an **LPM trie keyed by destination IPv4**
  (supports both single-IP `/32` and CIDR services). Its value carries **only what lookup+redirect need**:
  a service identity (`service_id`, u32) + an `enabled` flag. Plan/VIP/rule fields are added by their
  owning M3 features. To distinguish `service_miss` (no entry) from `service_disabled` (entry with
  `enabled=false`), **disabled services are present in the map** with the flag cleared — not absent.
  `SLRD-01..04`.
- **A-SLRD-2 (two new drop reasons):** This feature adds `DR_SERVICE_MISS` and `DR_SERVICE_DISABLED` to
  the shared `enum drop_reason` and records them in `counter_map`, fitting within packet-parse's
  `DROP_REASON_CAP=32` headroom (**no resize**, A-PKT-3). The **full** §10.2 reason set, ringbuf/perf
  **sampling**, and bloom false-positive counters remain the *Drop-reason counters* feature (M2 #3).
  `SLRD-05`.
- **A-SLRD-3 (`OUT` interface is loader config):** The loader gains an **`OUT` interface** argument/env
  (extends packet-parse's `IN`-only loader, A-PKT-6), resolves its ifindex, and populates `tx_devmap`.
  `IN` is where the program attaches; `OUT` is the redirect target. `SLRD-11`.
- **A-SLRD-4 (slotting mechanism = Design call):** The concrete double-buffer implementation
  (map-in-map `ARRAY_OF_MAPS` selected by `active_slot`, vs. adding a slot dimension to the key) is
  decided in Design; the **contract** fixed now is "slot-aware, selectable by `active_slot`, flippable by
  a single `active_slot` write." `SLRD-15`.
- **A-SLRD-5 (seed path is userspace, throwaway):** The pre-M4 seed helper lives in the loader/test
  (fills a slot's `service_map` + sets `active_config.active_slot`); it is **not** a Redis/DB path and is
  superseded by the M4 worker. The map/pin contract it populates is permanent. `SLRD-17`.
- **A-SLRD-6 (header preservation = no L3 mutation):** Redirect performs **no** TTL decrement and **no**
  IPv4 checksum recompute; the frame (incl. VLAN/QinQ tags) is forwarded **verbatim** via
  `bpf_redirect_map(&tx_devmap, ...)`. Transparent bridge, not a router (§6.2/§8.2). `SLRD-08..09`.
- **A-SLRD-7 (fail-closed on map/devmap errors):** A `service_map` **read error** (not a clean miss) and
  an **empty/misconfigured `tx_devmap`** both **fail closed** — dropped with `DR_MAP_ERROR`, never leaked
  to the host stack or an unconfigured `OUT` (§11.3). `SLRD-06`, `SLRD-10`.
- **A-SLRD-8 (live smoke is privileged & gated):** The two-veth live-forward test requires
  `CAP_NET_ADMIN`/root + a BPF-capable kernel, is **not** parallel-safe, and runs in the `full`
  gate/manually — a candidate first `dp-integration` entry in `TESTING.md`. `SLRD-24..25`.

---

## Cross-feature effects

- **Consumes:** packet-parse's `pkt_meta` / `enum drop_reason` / `record_drop` / `counter_map` / loader /
  `BPF_PROG_TEST_RUN` harness; **replaces** its service-lookup and ARP seams (PKT-15, PKT-23/24).
- **Establishes:** the **config-map + `active_slot` pin** pattern (`service_map`, `active_config`) and the
  **`tx_devmap` redirect** helper — reused by every M3 config lookup (whitelist/blacklist/rules/buckets)
  and by M4's build/swap.
- **Hands to M4:** a stable slot-aware map contract the worker fills from Postgres and flips atomically;
  M4 replaces the userspace seed helper, adds the DB build/verify + `ACTIVE_SLOT_SWAP` + rollback.
- **Hands to M3:** the pinned-slot invariant and the enabled-hit hand-off point where ingress-cap →
  whitelist/VIP → blacklist → rules → fairness are inserted before redirect.
- **No control-plane change.** No DB, no Redis, no API surface. Independent of the M1 work.
</content>
