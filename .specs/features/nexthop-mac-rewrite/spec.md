# Static Next-Hop L2 (MAC) Rewrite from a BPF Map Specification

**Feature slug:** `nexthop-mac-rewrite`
**ID category:** `NHR`
**Status:** Implemented and Verified
**Supersedes:** the packet-time `bpf_fib_lookup` mechanism shipped as AD-DP-01 / `service-lookup-redirect` amendment **SLRD-27..29**.

---

## Problem Statement

Clean traffic today is L2-rewritten at packet time by `l3_rewrite_nexthop()` calling
`bpf_fib_lookup(BPF_FIB_LOOKUP_DIRECT)` in the XDP hot path, with a **verbatim fallback** on any
non-`SUCCESS` result. This couples per-packet correctness to live kernel FIB/neighbor state, adds a
helper call to every redirected frame, and — through the verbatim fallback — silently forwards
**wrong-dst-MAC** frames when resolution is unavailable (exactly the routed-L3 failure AD-DP-01 was
meant to fix; the fallback just hides it). We want the next-hop MAC resolved **out of band** by the
control-plane/agent and stored in a BPF map, so the hot path is a pure map lookup + `memcpy` with **no
kernel FIB call and no verbatim fallback** — and unresolved traffic is dropped, not mis-forwarded.

## Goals

- [ ] Hot-path clean/VIP redirect rewrites L2 (dst + src MAC) from a **pre-populated BPF map**, with
      **zero** `bpf_fib_lookup` calls on the clean path.
- [ ] Next-hop MAC is resolved by a control-plane/agent lane and **written into the map before/at the
      moment a service is declared** (immediate resolve at apply), then **refreshed every 30 minutes**.
- [ ] A frame that matches an enabled service but has **no fresh resolved MAC** is **dropped
      fail-closed** with a new, exact drop-reason counter — no wrong-MAC frame ever leaves `OUT`.
- [ ] A protected service's destination is a **single IPv4 host**, not a CIDR range.

## Out of Scope

Explicitly excluded. Documented to prevent scope creep.

| Feature | Reason |
| ------- | ------ |
| IPv6 next-hop / neighbor discovery (NDP) | Data plane is IPv4-only in v1 (CM-02 is an M7/GA item). |
| Configurable **per-service next-hop / router IP** (backend behind an L3 hop, ECMP) | Decision: resolve the **service IP itself** on `OUT` (directly-connected backend). A per-service `next_hop_ip` is a **deferred idea**, not this feature. |
| **Bypass-mode** MAC handling for non-service traffic | Bypass forwards *all* traffic with no per-service context, so the per-service map cannot key it. Its L2 behavior is **flagged for Design** (see Edge Cases), not decided here. |
| HA/failover MAC / gratuitous-ARP takeover | GA (M7 / CM-01). |
| Removing the `bpf_fib_lookup` helper from the codebase entirely | This feature removes it from the **clean/VIP redirect path**; the bypass-path decision (above) governs whether it is fully deleted. |
| Reading MACs from the kernel neighbor cache (netlink) | Decision: the lane sends **active ARP probes**. Netlink harvesting is an alternative transport, deferred. |

---

## User Stories

### P1: Static map-based L2 rewrite on the clean path ⭐ MVP

**User Story**: As the data-plane, I want to rewrite the egress dst/src MAC from a pre-resolved BPF map
so that clean frames reach the backend at L2 without any per-packet kernel FIB lookup.

**Why P1**: This is the core mechanism change; it is the reason the feature exists.

**Acceptance Criteria**:

1. WHEN a clean (or VIP) IPv4 frame is admitted and enters `redirect_out()` THEN the system SHALL look
   up the matched service's next-hop entry (keyed by `pkt_meta.service_id` / `dp_id`) and, if the entry
   is **present and marked resolved**, rewrite `eth->h_dest ← entry.dst_mac` and
   `eth->h_source ← entry.src_mac` **before** `bpf_redirect_map(&tx_devmap, …)`.
2. WHEN the rewrite is performed THEN the system SHALL make **no** `bpf_fib_lookup` call and SHALL leave
   the IP header (dst, ports, **TTL**, checksum) unchanged — L2 only (preserves SLRD-08).
3. WHEN the matched-service next-hop entry is **absent** or **not marked resolved** THEN the system SHALL
   drop the frame with a new `DR_NEXTHOP_UNRESOLVED` reason and SHALL NOT emit the frame on `OUT`.
4. WHEN the frame is ARP (SLRD-19) or otherwise not a matched-service IPv4 frame THEN the system SHALL
   **not** apply the rewrite (ARP is forwarded verbatim as today).

**Independent Test**: `BPF_PROG_TEST_RUN` a SYN to a declared service with a seeded resolved entry →
retval `XDP_REDIRECT`, output dst MAC = entry.dst_mac, src MAC = entry.src_mac, IP/port/TTL intact; then
clear the entry (unresolved) and re-run → retval `XDP_DROP` and `nexthop_unresolved` counter +1.

---

### P1: Fail-closed on unresolved next-hop ⭐ MVP

**User Story**: As an operator, I want unresolved traffic dropped (not mis-forwarded) so that a stale or
missing neighbor never causes silent wrong-MAC delivery.

**Why P1**: Directly encodes the user's decision to remove the verbatim fallback; it is the safety
contract of the whole feature.

**Acceptance Criteria**:

1. WHEN `DR_NEXTHOP_UNRESOLVED` is introduced THEN it SHALL be **appended** to the frozen §9.2
   `enum drop_reason` at index **16** (`DROP_REASON_COUNT` 16→17, within `DROP_REASON_CAP=32`), with a
   matching `drop_reason_name` entry — existing indices unchanged (append-only ABI).
2. WHEN a matched-service frame is dropped for want of a resolved MAC THEN the system SHALL increment the
   exact per-CPU `counter_map[DR_NEXTHOP_UNRESOLVED]` and the per-service `svc_stat` drop bucket, via the
   existing `record_drop(meta, reason)` choke point.
3. WHEN resolution later succeeds for that service THEN subsequent frames SHALL be redirected normally
   with no reload (the map entry flip is observed live on the hot path).

**Independent Test**: unit-assert the enum/name-table append + `_Static_assert`; `BPF_PROG_TEST_RUN`
proves the drop + exact counter increment; a second run with a seeded entry proves live recovery.

---

### P1: Service destination is a single IPv4 host ⭐ MVP

**User Story**: As the control-plane, I want a service's destination to be exactly one IPv4 address so
that each service maps to exactly one next-hop entry.

**Why P1**: The map is keyed one-entry-per-service; a CIDR service would need many next-hops. Also aligns
the CP model with the data plane, which already looks up services at a fixed `/32` LPM key.

**Acceptance Criteria**:

1. WHEN a tenant/admin creates or updates a `ProtectedService` THEN the control-plane SHALL accept the
   destination **only** if it is a single IPv4 host (a `/32`, or a bare address), and SHALL reject a
   multi-address CIDR with a validation error.
2. WHEN the destination is validated THEN the existing global no-overlap and
   `cidr_in_tenant_allocation` (AUTH-14) checks SHALL still apply (a `/32` inside the tenant allocation).
3. WHEN the data plane looks up a service THEN it SHALL continue to use the `/32` LPM key already in
   place (no data-plane lookup change from this story).

**Independent Test**: API test — `POST /services` with `203.0.113.0/24` → 422; with `203.0.113.7` (or
`/32`) → 201; overlap/allocation guards still fire.

---

### P1: Node-global OUT source MAC ⭐ MVP

**User Story**: As the agent, I want the `OUT` port MAC captured once at node scope and stamped into
every next-hop entry so that egress frames carry a correct source MAC.

**Why P1**: Half of the L2 rewrite is the source MAC; it is a single node property, not per-service.

**Acceptance Criteria**:

1. WHEN the gateway loads (or the OUT interface is (re)configured) THEN the system SHALL read the `OUT`
   interface hardware address and use it as the **node-global** `src_mac` for all next-hop entries.
2. WHEN a next-hop entry is written THEN its `src_mac` SHALL be the current node-global `OUT` MAC.
3. WHEN the `OUT` interface MAC changes THEN the refresh lane SHALL re-read it and update entries on the
   next write (bounded by the refresh cadence; see edge cases).

**Independent Test**: loader/seed test confirms the pinned entry's `src_mac` equals the `OUT` iface MAC
reported by `if_nametoindex`/`ioctl(SIOCGIFHWADDR)`.

---

### P1: Immediate resolve at declare + 30-minute ARP-probe refresh ⭐ MVP

**User Story**: As the agent, I want to resolve a service's backend MAC immediately when the service is
declared and re-probe every 30 minutes so that the map is populated promptly and stays current.

**Why P1**: "Stored in the map after declaring the Service" + "updated every 30 minutes" is the explicit
control-plane requirement.

**Acceptance Criteria**:

1. WHEN a service is created, enabled, or has its destination changed (i.e. applied) THEN the
   control-plane SHALL trigger an **immediate** resolve of that service's IP on `OUT` and write the
   resulting entry, without waiting for the periodic tick.
2. WHEN the periodic lane ticks (default every **1800 s**, node-configurable) THEN it SHALL ARP-probe
   each **enabled** service's IP on `OUT` and refresh its entry.
3. WHEN an ARP probe succeeds THEN the lane SHALL write `{dst_mac = reply.sha, src_mac = OUT MAC,
   resolved = 1, last_resolved = now}` for that service's `dp_id`.
4. WHEN an ARP probe fails after a bounded number of retries within the tick THEN the lane SHALL mark the
   entry **unresolved** (fail-closed; last-known MAC is **not** retained), so the hot path drops per
   NHR-03. *(Bounded retry-before-invalidate avoids flapping on a single lost ARP; see edge cases.)*
5. WHEN a service is disabled or deleted THEN the lane SHALL **evict** that service's next-hop entry.
6. WHEN the node restarts THEN resolution SHALL re-run from the immediate-resolve + periodic paths (the
   entry is runtime state, re-derived; a restart does not require a config change to re-populate).

**Independent Test**: integration — declare a service against a fake/stub ARP responder → entry resolved
within seconds; advance the clock past the interval with the responder down → entry marked unresolved;
bring it back → resolved again; disable the service → entry evicted.

---

### P1: Next-hop map is runtime state, pinned, decoupled from config swap ⭐ MVP

**User Story**: As the data-plane, I want the next-hop map to be pinned runtime state that survives a
config double-buffer swap so that resolution (which is asynchronous) is never wiped by an unrelated
service/rule apply.

**Why P1**: MACs are resolved on a different cadence than config applies; a rebuild-per-job (M4 #2) must
not clear resolved MACs, and the writer must not race the applier's slot flip.

**Acceptance Criteria**:

1. WHEN the loader initializes THEN it SHALL create and **pin** the next-hop map under
   `/sys/fs/bpf/xdp_gateway/` alongside the other pinned maps.
2. WHEN a config double-buffer apply (`xdpgw-apply` / `active_config` flip) runs THEN it SHALL **not**
   rebuild or clear the next-hop map (it is **unslotted** runtime state, keyed by `dp_id`, like the rate
   buckets — §8.3), so an already-resolved MAC survives the swap.
3. WHEN the resolver lane writes an entry THEN it SHALL do so via a **privileged data-plane writer** (a
   `dpstat`-family subcommand, mirroring the M6 `dpstat set-bypass` writer pattern) invoked by the
   worker, not by fabricating map bytes in the hot path.

**Independent Test**: privileged smoke — resolve an entry, run a full `xdpgw-apply` swap, confirm the
entry is still resolved and the hot path still rewrites; kill+reload loader → map re-pinned.

---

### P2: Observability of resolution state

**User Story**: As an operator, I want to see which services have a resolved next-hop and how many
frames are dropped for want of one so that I can detect a broken backend/segment quickly.

**Why P2**: The MVP forwards/drops correctly without a UI; visibility accelerates operations but is not
required to be correct.

**Acceptance Criteria**:

1. WHEN an operator runs the reader CLI THEN `dpstat` SHALL expose a next-hop section: per `dp_id` →
   `dst_mac`, `src_mac`, `resolved`, and `last_resolved` age, plus the node `nexthop_unresolved` counter.
2. WHEN node health is read THEN `/node/health` (or telemetry) SHALL surface the count of enabled
   services currently **unresolved** (a fail-closed blackhole indicator).
3. WHEN a service transitions resolved→unresolved (or vice-versa) THEN the event SHALL be observable
   (log/telemetry) so M6 *Alerting* can bind a rule to it (alert wiring itself is out of scope here).

**Independent Test**: seed mixed resolved/unresolved entries → `dpstat` prints them correctly; `/node/health`
reports the unresolved count; drop counter increments on an unresolved hit.

---

### P3: Manual re-resolve trigger & resolve metrics

**User Story**: As an admin, I want to force an immediate re-resolve of a service and see resolve
success metrics so that I can recover from a transient neighbor failure without waiting 30 minutes.

**Why P3**: A convenience over the automatic immediate-resolve + periodic refresh already in P1.

**Acceptance Criteria**:

1. WHEN an admin triggers "resolve now" for a service THEN the lane SHALL re-probe that service's IP out
   of band and update the entry.
2. WHEN resolve metrics are read THEN the system SHALL expose per-service resolve success/failure counts
   and last-resolved age.

---

## Edge Cases

- WHEN a service is declared but the first resolve has not yet completed THEN matched frames SHALL be
  dropped `nexthop_unresolved` (fail-closed) until the entry is written — no verbatim leak.
- WHEN a single ARP probe is lost but the neighbor is actually up THEN the lane SHALL retry a bounded
  number of times within the tick **before** invalidating, so one lost probe does not blackhole a live
  service (fail-closed is eventual, not single-packet-triggered).
- WHEN the backend's MAC changes (NIC swap, VM migration) THEN the next successful probe (≤ the interval,
  or immediately on a manual/apply trigger) SHALL update the entry; frames in the gap are dropped
  fail-closed rather than sent to the old MAC.
- WHEN the `OUT` interface MAC changes or the link flaps THEN the node-global `src_mac` SHALL be re-read
  and re-stamped on the next write.
- WHEN a service's `dp_id` is reused after deletion THEN a stale next-hop entry SHALL NOT be served —
  deletion evicts the entry (NHR-11).
- WHEN the node is in **global bypass mode** THEN the L2 behavior of bypass-forwarded (non-service)
  traffic is **undecided and flagged for Design** — candidate options: forward **verbatim** (transparent
  emergency mode), or resolve/serve a single node-global bypass next-hop, or retain `bpf_fib_lookup` on
  the bypass path only. *(Bypass has no per-service context, so it cannot use the per-`dp_id` map.)*
- WHEN existing `ProtectedService` rows have a non-`/32` (CIDR) destination at rollout THEN the migration
  behavior (reject-at-migrate vs. flag vs. accept-first-address) MUST be decided at Design; no
  fabricated conversion.

---

## Requirement Traceability

| Requirement ID | Story | Phase | Status |
| -------------- | ----- | ----- | ------ |
| NHR-01 | P1: Static map rewrite | Design | Pending |
| NHR-02 | P1: Static map rewrite (no fib_lookup, TTL preserved) | Design | Pending |
| NHR-03 | P1: Fail-closed (`DR_NEXTHOP_UNRESOLVED` + counter) | Design | Pending |
| NHR-04 | P1: ARP/non-matched frames not rewritten | Design | Pending |
| NHR-05 | P1: Drop-reason ABI append (index 16, name table) | Design | Pending |
| NHR-06 | P1: Live recovery on later resolve | Design | Pending |
| NHR-07 | P1: Single-IPv4-host service destination | Design | Pending |
| NHR-08 | P1: Host dest still honors overlap/allocation guards | Design | Pending |
| NHR-09 | P1: Node-global OUT src MAC | Design | Pending |
| NHR-10 | P1: Immediate resolve at declare/apply | Design | Pending |
| NHR-11 | P1: 30-min periodic ARP-probe refresh (configurable) | Design | Pending |
| NHR-12 | P1: Probe success → write entry | Design | Pending |
| NHR-13 | P1: Probe fail (bounded retry) → unresolved / fail-closed | Design | Pending |
| NHR-14 | P1: Disable/delete → evict entry | Design | Pending |
| NHR-15 | P1: Map pinned + unslotted, survives config swap | Design | Pending |
| NHR-16 | P1: Privileged DP writer (dpstat-family), not hot-path | Design | Pending |
| NHR-17 | P2: `dpstat` next-hop dump + unresolved counter | - | Pending |
| NHR-18 | P2: `/node/health` unresolved-service count | - | Pending |
| NHR-19 | P2: resolved↔unresolved transition observable (alert hook) | - | Pending |
| NHR-20 | P3: manual "resolve now" trigger | - | Pending |
| NHR-21 | P3: per-service resolve metrics | - | Pending |

**ID format:** `NHR-[NUMBER]`
**Status values:** Pending → In Design → In Tasks → Implementing → Verified
**Coverage:** 21 total, 0 mapped to tasks yet (Design pending)

---

## Success Criteria

- [ ] On the routed `cyberrange02`-style deployment, a clean SYN to a declared service is redirected with
      **dst MAC = backend MAC, src MAC = OUT MAC**, IP/port/TTL intact, and the backend serves HTTP 200 —
      **with the `bpf_fib_lookup` call removed** from the clean path.
- [ ] A frame to a declared-but-unresolved service is **dropped** (`nexthop_unresolved` +1), never sent
      to `OUT` with a wrong MAC.
- [ ] A service's MAC appears in the map **within seconds** of declaration (immediate resolve) and is
      refreshed on a **≤30-minute** cadence.
- [ ] `POST /services` rejects a multi-address CIDR destination and accepts a single host.
- [ ] Data-plane `make test` stays green (new `nexthop_unresolved` + rewrite cases added); the frozen
      drop-reason ABI grows append-only.
