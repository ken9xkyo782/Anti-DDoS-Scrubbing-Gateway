# Static Next-Hop L2 (MAC) Rewrite — Context

**Gathered:** 2026-07-20
**Spec:** `.specs/features/nexthop-mac-rewrite/spec.md`
**Status:** Ready for design

---

## Feature Boundary

Replace the packet-time `bpf_fib_lookup` L2 rewrite (SLRD-27..29 / AD-DP-01) with a **static, map-based**
next-hop rewrite: the control-plane/agent resolves each single-IP service's backend MAC out of band and
stores `{dst_mac, src_mac}` in a pinned BPF map; the XDP hot path does a pure lookup + `memcpy` with **no
kernel FIB call and no verbatim fallback**, dropping fail-closed when unresolved. Covers the data-plane
hot path + map, the drop-reason ABI, the CP single-IP service constraint, and a worker ARP-probe
resolution lane (immediate at declare + 30-min refresh). It does **not** cover IPv6/NDP, per-service
router next-hops, or bypass-mode MAC handling.

---

## Implementation Decisions

### Behavior on missing / stale MAC (D-NHR-1) — **Fail-closed drop**

- A matched-service frame with **no fresh resolved entry** (never resolved, or invalidated after a failed
  refresh) is **dropped** with a new `DR_NEXTHOP_UNRESOLVED` reason + exact per-CPU counter.
- **No wrong-MAC frame ever leaves `OUT`.** The old verbatim fallback (SLRD-28) is removed.
- **Last-known MAC is NOT retained** across a failed refresh — a failed probe invalidates the entry.
  (To avoid flapping on a single lost ARP, the lane does a **bounded retry within a tick before
  invalidating** — eventual fail-closed, not single-packet-triggered. Retry count/timeout = design-tunable.)

### Next-hop target & source MAC (D-NHR-2) — **Service IP itself; OUT MAC node-global**

- The scanner resolves the service's **own destination IP** on the `OUT` segment (directly-connected
  backend — matches the verified `cyberrange02` deploy where `fib_lookup` resolved the backend MAC
  directly). → `dst_mac` is **per-service**.
- `src_mac` = the **`OUT` port hardware address**, a single **node-global** value read from the OUT
  interface and stamped into every entry.
- A configurable per-service `next_hop_ip` (backend behind an L3 router) is **explicitly deferred**.

### Resolution transport & failure semantics (D-NHR-3) — **Active ARP probes**

- The lane **sends its own ARP request packets** on `OUT` and parses replies (self-contained; does not
  depend on the kernel neighbor-cache state). Requires raw-socket / `CAP_NET_RAW` privilege.
- A successful reply → write `{dst_mac = reply.sha, src_mac = OUT MAC, resolved = 1, last_resolved}`.
- A failed probe (after bounded retry) → mark **unresolved** → hot path fail-closed (per D-NHR-1).

### Initial population timing (D-NHR-4) — **Resolve immediately at apply**

- Declaring / enabling / re-targeting a service **kicks an immediate resolve** so its MAC is populated
  within seconds; the 30-minute lane only **refreshes** thereafter.
- Minimizes the fail-closed drop window for freshly-declared services.

### Map placement (locked as behavior, exact shape → Design)

- The next-hop map is **runtime state**, pinned, **unslotted** (keyed by `dp_id`), and is **NOT** rebuilt
  or cleared by the M4 #2 config double-buffer swap — resolution survives an unrelated config apply.
- The privileged writer is a **`dpstat`-family subcommand** invoked by the worker (mirrors the M6
  `dpstat set-bypass` / `NodeControlReconciler` writer pattern), **not** the `xdpgw-apply` config helper.

### Agent's Discretion (design-time choices)

- Exact map value layout (`dst_mac[6]`, `src_mac[6]`, `resolved`/flags byte, `last_resolved` — and
  whether `src_mac` is stored per-entry or read from a separate node-global map).
- Retry count / probe timeout / per-tick pacing for the ARP lane.
- Whether the resolver lane is a new worker background asyncio task (like the feed/telemetry/billing/
  node-control lanes) or folded into `NodeControlReconciler`.
- The `dpstat` subcommand surface name(s) and the `/node/health` field shape.

---

## Specific References

- Current mechanism to replace: `data-plane/src/xdp_gateway.bpf.c` `l3_rewrite_nexthop()` (lines ~148–173),
  called from `redirect_out()` and `node_control.h` `redirect_out_bypass()`.
- Frozen ABI to append to: `data-plane/src/drop_reason.h` — add `DR_NEXTHOP_UNRESOLVED = 16`
  (`DROP_REASON_COUNT` 16→17, cap 32), plus `drop_reason_name` entry. Append-only, like the historical
  `map_error` 4→15 move.
- `service_val` is **frozen at 8 bytes** (M4 contract, `_Static_assert`) → MACs need a **new map**, not a
  field on `service_val`.
- Writer pattern to mirror: M6 `dpstat set-bypass` + `worker/node_control_reconciler.py`
  (`DpstatBypassWriter`) — a background lane that execs a privileged `dpstat` subcommand on drift.
- CP model to constrain: `control-plane/app/db/models.py:498` `ProtectedService.cidr_or_ip` (currently
  `CIDR`); the DP already looks services up at a fixed `/32` LPM key.
- Verified deploy that validates D-NHR-2: STATE.md AD-DP-01 — backend `fa:16:3e:ce:1f:88` at
  `118.107.78.137` directly connected on `OUT`, `fib_lookup` resolved its MAC directly.

---

## Flagged for Design (open, not decided)

- **Bypass-mode L2 behavior.** Global bypass forwards *all* traffic with no per-service context, so it
  cannot key the per-`dp_id` map. Options: verbatim forward (transparent emergency mode) / a node-global
  bypass next-hop entry / retain `bpf_fib_lookup` on the bypass path only. **Must be resolved at Design.**
- **Existing non-`/32` service rows at rollout.** Reject-at-migration vs. flag vs. accept-first-address —
  decide at Design; do not fabricate a conversion.
- **`ixgbe` OUT XDP-TX ring** follow-up (STATE.md AD-DP-01 open item #1: loader auto-attach `XDP_PASS`
  on OUT). Not owned by this feature but the smoke test depends on it; note the dependency.

---

## Deferred Ideas

- Configurable per-service `next_hop_ip` for backends behind an L3 router / ECMP next-hop selection.
- Harvesting MACs from the kernel neighbor cache via netlink (`RTM_GETNEIGH`) instead of active ARP.
- Gratuitous-ARP / HA failover MAC takeover (M7 / CM-01).
- IPv6 next-hop via NDP (M7 / CM-02).
