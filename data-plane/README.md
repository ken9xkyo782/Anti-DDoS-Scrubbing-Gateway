# Data Plane

Native-XDP packet parsing and fail-fast verdict code lives here.

## Build

```bash
make bpf skel loader dpstat
```

This compiles `src/xdp_gateway.bpf.c` with `clang -target bpf` and generates a libbpf skeleton at
`build/xdp_gateway.skel.h`. The loader binary is written to `build/xdp_gateway_loader`, and the
operator CLI is written to `build/dpstat`.

## Native XDP Loader

Run the loader with IN and OUT interface arguments, or `IN_IFACE` / `OUT_IFACE`:

```bash
sudo ./build/xdp_gateway_loader <in-veth> <out-veth>
IN_IFACE=<in-veth> OUT_IFACE=<out-veth> sudo ./build/xdp_gateway_loader
make run IFACE=<in-veth> OUT=<out-veth>
```

The loader attaches to IN with `XDP_FLAGS_DRV_MODE` only and populates `tx_devmap[0]` with the OUT ifindex.
If IN does not support native XDP, if OUT cannot be resolved/populated, or a map seed fails, it exits with
a clear error and does not fall back to generic/SKB mode. Press Ctrl-C to detach; `ip link show <in-veth>`
should then show no XDP program on the interface.

The loader pins observability maps under `/sys/fs/bpf/xdp_gateway/` and refuses to start if that
directory already exists. Remove stale pins before loading a new gateway. On normal Ctrl-C detach, the
loader unpins the maps and removes the directory.

For a demo service before the M4 worker exists, set `SERVICE_DEST` to an IPv4 address or canonical CIDR.
An address without a prefix is seeded as `/32`; invalid, IPv6, or non-canonical CIDRs are rejected.
When `SERVICE_DEST` is set, the loader also seeds a no-quota match-all allow-rule block into both rule
slots. Without `SERVICE_DEST`, `active_config` is still seeded and the service map stays empty, so valid
IPv4 traffic fails closed as `service_miss`.

Optional whitelist seed variables let you demo VIP bypass before the M4 worker exists:

- `XDPGW_SEED_WL_CIDR=<source-cidr>` seeds scoped whitelist bloom/LPM entries in both slots.
- `XDPGW_SEED_VIP_PPS=<packets-per-second>` sets the aggregate VIP PPS ceiling.
- `XDPGW_SEED_VIP_BPS=<bytes-per-second>` sets the aggregate VIP byte ceiling.

If you set `XDPGW_SEED_WL_CIDR` without either VIP ceiling variable, the loader uses
`XDPGW_SEED_VIP_PPS=1000`. This prevents a ceiling-less ACTIVE whitelist state.

```bash
SERVICE_DEST=10.0.0.2 sudo ./build/xdp_gateway_loader <in-veth> <out-veth>
SERVICE_DEST=10.0.0.2 XDPGW_SEED_WL_CIDR=10.0.0.1/32 XDPGW_SEED_VIP_PPS=1000 sudo ./build/xdp_gateway_loader <in-veth> <out-veth>
SERVICE_DEST=10.0.0.0/24 make run IFACE=<in-veth> OUT=<out-veth>
```

Optional deny seed variables let you demo blacklist behavior before the M4 worker exists:

- `XDPGW_SEED_GBL_CIDR=<source-cidr>` seeds one global blacklist CIDR in slot 0.
- `XDPGW_SEED_BLOCKED_PORT=<udp-source-port>` sets one slot-0 UDP blocked-port bitmap bit.

The loader seed path is intentionally small. It writes one CIDR or port for smoke testing and leaves
the 16..23 global-bloom expansion band, snapshot replacement, and bulk map population to the M4
builder.

## Forwarding: map-based next-hop L2 rewrite (supersedes packet-time fib_lookup)

`redirect_out` looks up the matched service's next-hop entry in the pinned, unslotted `nexthop_map` (keyed by `dp_id`), rewrites `eth->h_dest` to `dst_mac` and `eth->h_source` to `src_mac`, then redirects via `bpf_redirect_map(&tx_devmap, 0, XDP_DROP)`.

- **Out-of-band resolution:** The control-plane worker (or `dpstat resolve-nexthop <dp_id> <ipv4>`) resolves next-hop MACs via ARP on `OUT` and populates `nexthop_map`.
- **Fail-closed:** An unresolved or absent next-hop entry drops the frame with `DR_NEXTHOP_UNRESOLVED` (index 16) before clean statistics are recorded — mis-forwarding wrong-MAC frames is prevented.
- **Single-host service:** Protected service destinations are single IPv4 host addresses (`/32` or bare host).
- **Bypass mode:** Soft-bypass forwards traffic verbatim with no L2 rewrite or FIB lookup.
- **Zero packet-time FIB lookup:** `bpf_fib_lookup` is superseded and removed from the redirect path. TTL and IPv4 checksum remain intact.

`dpstat` subcommands:
- `dpstat resolve-nexthop <dp_id> <ipv4>`: ARP-probes target on OUT interface and writes resolved entry.
- `dpstat evict-nexthop <dp_id>`: deletes map entry on service disable/delete.
- `dpstat set-nexthop <dp_id> <dst_mac> [<src_mac>]`: manual next-hop MAC seed for testing/ops.
- `dpstat nexthop`: dumps next-hop entries and resolution status.

### OUT interface must allow XDP TX (devmap redirect target)

Some NIC drivers (e.g. Intel `ixgbe`, `i40e`, `ice`) only allocate XDP TX rings when an XDP program is
attached to the interface. The loader attaches XDP to **IN only**, so a devmap redirect to such an OUT
interface fails at `ndo_xdp_xmit` — packets are counted `clean` (the verdict) but dropped silently at
egress and `xdp:xdp_redirect_err` increments. Workaround until the loader auto-attaches: put a minimal
`XDP_PASS` program on OUT in native mode:

```bash
sudo ip link set dev <out-iface> xdpdrv obj <xdp_pass.o> sec xdp   # detach: xdpdrv off
```

`veth` (used by the smoke tests) does not need this, which is why the issue only surfaces on real NICs.

## Allow rules and tunnel traffic

`src/rules.h` is the M4 map-build contract for allow rules. Rule blocks must be pre-sorted by ascending
priority, because the hot path treats array position as first-match order. The `bps` field is bytes per
second; the future worker must convert any control-plane unit before writing the map.

**Per-rule `pps`/`bps` rate-limits are NOT plumbed through the control-plane apply path.** The v2 apply
wire format (`src/apply_snapshot.h`, `APPLY_SNAPSHOT_RULE_SIZE == 10`) carries only ports, proto and
flags per rule — no rate values. A writer that sets `RULE_F_PPS_SET`/`RULE_F_BPS_SET` without also
seeding tokens makes `rl_bucket_consume` admit nothing, so **100% of that rule's traffic drops as
`rate_limit_drop`** (verified 2026-07-20 on service `118.107.78.137:2283`). The control-plane worker
(`_rule_flags`) therefore emits only `RULE_F_ENABLED`; per-rule rate-limits are a no-op until the wire
format is extended (schema bump). Use the service **VIP ceiling** (`vip_pps`/`vip_bps`, which *is*
carried) or the **`ServicePlan`** committed/ceiling for bandwidth control. The demo loader can still
seed per-rule limits directly into the map struct for data-plane testing.

`RULE_PROTO_ANY` matches only TCP, UDP, and ICMP. GRE, ESP, and other non-TCP/UDP/ICMP IPv4 protocols
are unmatchable in v1 and drop with `not_allowed`, even when a service has a match-all `any` rule.
Sustained `not_allowed` from tunnel traffic is expected behavior, not a loader or map-seeding failure.

## Fairness and bandwidth reservation

`src/fairness.h` defines the M4 map-build contract for clean-traffic fairness. When the demo
loader seeds `SERVICE_DEST`, it writes a `fair_config` row for that service and a
`fair_node_config` row for both slots. These values are already in bytes per second; the data
plane only consumes the precomputed budgets.

The loader accepts these optional fairness seed variables:

| Variable | Default | Purpose |
| --- | --- | --- |
| `XDPGW_FAIR_COMMITTED_BPS` | `12500000000` B/s (100 Gbps) | Per-service exact committed rate. |
| `XDPGW_FAIR_CEILING_BPS` | `12500000000` B/s (100 Gbps) | Per-service clean ceiling; burst is `ceiling - committed`. |
| `XDPGW_NODE_CLEAN_CAPACITY_BPS` | `5000000000` B/s (40 Gbps) | Node clean-capacity input for burst headroom. |
| `XDPGW_FAIR_K` | `3` | Multiplier for the raw ingress-cost cap. |
| `XDPGW_FAIR_REF_PKT` | `512` bytes | Reference packet size used to derive the cap PPS budget. |

The loader rejects `committed > ceiling`, `K=0`, and `REF_PKT=0`. It derives the dual ingress cap
as `cap_bps = min(FAIR_RATE_MAX, K * ceiling_bps)` and
`cap_pps = cap_bps / REF_PKT`, using overflow-safe multiplication. Every emitted fairness rate is
clamped to `FAIR_RATE_MAX = 16000000000` B/s (128 Gbps), which keeps refill arithmetic inside its
supported range.

For clean non-VIP traffic, the ladder first consumes the service's global, spin-locked committed
bucket. That bucket is exact across CPUs; a successful committed admit bypasses the remaining
buckets. If it is empty, the packet consumes the service's per-CPU burst bucket, then the shared
per-CPU node-headroom bucket. An empty service bucket drops `service_ceiling_drop`; an empty node
bucket drops `congestion_drop`. The burst draw is deliberately service-then-node: a node miss does
not refund the service-burst token.

The raw ingress cap runs immediately after enabled-service lookup, before whitelist, deny, or rule
lookups. It is destination-service keyed and enforces both PPS and byte budgets; an over-cap packet
drops `ingress_cap_drop`. Therefore VIP traffic remains subject to the ingress cap. A VIP packet
that passes the cap and its own VIP ceiling redirects from the whitelist stage and never enters the
clean-traffic ladder.

Node headroom is `max(0, node_clean_capacity - sum(committed))`. If commitments exceed the node
capacity, headroom is zero: committed traffic still uses its exact bucket, while burst traffic sheds
as `congestion_drop` once it reaches the node bucket. The default seed has equal committed and
ceiling rates, so its burst rate is zero and the zero default headroom is harmless.

The TDD name `service_agg_rate_state` is realized by two maps: `svc_committed_state` holds the
exact global committed state, and `svc_burst_state` holds the per-CPU service-burst state.
`node_burst_state` is the shared per-CPU node-headroom state, and
`service_ingress_cap_state` holds the per-CPU dual cap state. These fairness maps are not part of
the loader's pinned observability-map interface.

This mechanism guarantees committed clean bandwidth per service, not unlimited classification
capacity. A PPS flood beyond a node's physical classification capacity is bounded by the ingress
cap but can still contend for CPU. That residual single-node limitation requires capacity planning
and HA/scale-out; it does not change the committed-bandwidth guarantee or imply absolute
availability under an extreme flood.

## Whitelist and VIP ceiling

`src/whitelist.h` is the M4 map-build contract for scoped whitelist and VIP ceiling config. A
whitelist entry only applies to its own `service_id`; it never edits or weakens global deny maps.

A whitelist requires a VIP ceiling to take effect. If both `vip_pps` and `vip_bps` are NULL in config,
the builder must leave `WL_F_ACTIVE` unset and the data plane treats the whitelist as inert. One set
dimension is enough: the unset dimension is unlimited, while `0` means explicit block for that
dimension.

Whitelist bloom filters are replace-only. The future worker must build fresh bloom inners for the
inactive slot, keep bloom contents a superset of the LPM entries, then swap the inactive slot into
service. It must not try to clear or delete individual bloom values in place.

The VIP ceiling is aggregate per service. A spoofed flood that matches a whitelisted source can exhaust
that service's VIP budget and cause legitimate VIP traffic to drop with `vip_ceiling_drop`. This is the
bounded BL-08 residual self-DoS mode; alerting on repeated VIP ceiling hits belongs to M6.

## Deny filters and blacklist

`src/blacklist.h` is the M4 map-build contract for global blacklist maps (service-scoped blacklist superseded and removed in feature `service-blacklist-removal`). The deny
stage runs after whitelist miss and before allow rules, so a whitelist hit can bypass deny filters only
when the service has an active whitelist and VIP ceiling.

The data plane drops UDP packets from these source ports before checking dynamic maps:
`17`, `19`, `53`, `111`, `123`, `137`, `161`, `389`, `520`, `1900`, `5353`, and `11211`.
These ports cover common reflection and amplification sources. If a service legitimately receives
UDP from sources such as resolvers or NTP servers, whitelist those upstream source CIDRs and configure
a VIP ceiling. A whitelist without an active VIP ceiling is inert.

The data plane drops source addresses in these bogon ranges:

- `0.0.0.0/8`
- `10.0.0.0/8`
- `100.64.0.0/10`
- `127.0.0.0/8`
- `169.254.0.0/16`
- `172.16.0.0/12`
- `192.0.0.0/24`
- `192.0.2.0/24`
- `192.168.0.0/16`
- `198.18.0.0/15`
- `198.51.100.0/24`
- `203.0.113.0/24`
- `224.0.0.0/4`
- `240.0.0.0/4`

The UDP blocked-port bitmap is a seed-only demo surface until the M4 builder lands. Use
`XDPGW_SEED_BLOCKED_PORT` for a single slot-0 port in smoke tests. Production snapshots must build
fresh inactive-slot bitmap and blacklist maps, then swap slots in one active-config update.

Run the gated scale check when you change blacklist capacity or map shape:

```bash
sudo make blbulk
```

The July 9, 2026 gate inserted 1,048,576 global blacklist entries and 1,048,576 bloom keys. It
measured `cgroup_delta_kib=147364`, `rss_delta_kib=0`, and `13631488` deterministic key/value bytes;
sampled `/24` and `/32` hits, a miss, bloom membership, and one XDP drop verdict all passed.

## Drop counters and sampling

`src/drop_reason.h` is the source of truth for the frozen drop-reason ABI. Reasons `0..15` are fixed
in TDD §9.2 order and future reasons must append within `DROP_REASON_CAP=32`.

The loader pins these maps for operators and later workers:

| Path | Purpose |
| --- | --- |
| `/sys/fs/bpf/xdp_gateway/counter_map` | Exact per-CPU drop totals by reason. |
| `/sys/fs/bpf/xdp_gateway/drop_ringbuf` | Rate-limited sampled drop events. |
| `/sys/fs/bpf/xdp_gateway/sample_config` | Runtime sample budget: `rate_per_sec` and `burst`. |
| `/sys/fs/bpf/xdp_gateway/sample_stats` | Per-CPU emitted, suppressed, and lost sample counters. |
| `/sys/fs/bpf/xdp_gateway/bloom_stats` | Per-stage bloom-hit/LPM-miss counters. |
| `/sys/fs/bpf/xdp_gateway/svc_stat_map` | Exact per-service clean/drop packet and byte counters, keyed by `dp_id`. |
| `/sys/fs/bpf/xdp_gateway/node_control` | Node-global soft-bypass flag. |
| `/sys/fs/bpf/xdp_gateway/bypass_counter` | Exact per-CPU bypass packet and byte totals. |

Use `dpstat` while the loader is running:

```bash
sudo ./build/dpstat counters
sudo ./build/dpstat counters -w 2
sudo ./build/dpstat tail
sudo ./build/dpstat rate 256 64
sudo ./build/dpstat set-bypass 1
sudo ./build/dpstat set-bypass 0
sudo ./build/dpstat set-blocked-ports 53 123 1900 11211
sudo ./build/dpstat set-blocked-ports
```

`set-blocked-ports` sets dynamic UDP source-port drop filters in the node-global bitmap:
- **Usage:** `dpstat set-blocked-ports <port...>` (0..65535). Running with no port arguments clears all dynamic blocked ports.
- **Both slots:** Writes both active and inactive slots of `udp_blocked_port_bitmap`, preserving dynamic blocked ports across `xdpgw-apply` service applies (slot flips).
- **Drift-only reconcile & loader-reload caveat:** `dpstat set-blocked-ports` mutates pinned BPF maps directly. If a gateway reload or machine reboot unpins/clears BPF maps, restart the control-plane worker to re-assert desired blocked ports.

Counter maps reset when the XDP program is reloaded. Consumers must compute deltas between reads, not
interpret values as lifetime totals. Sampling budget is per CPU; a rate of `256` permits up to
`256 * online_cpu_count` events per second across the node, with burst `64` per CPU by default. Exact
drop counters stay correct even when samples are suppressed or lost. `dpstat counters` also prints
the `bloom_hit_lpm_miss` rows for whitelist, global blacklist, and total.

## Per-service telemetry snapshot

`svc_stat_map` records exact per-CPU clean and dropped packet/byte counters for
matched services. Its key is the stable control-plane `dp_id`; packets that do
not match a service stay in the node-global counters. A reload clears this map,
so userspace consumers must calculate counter deltas and treat a decrease as a
reset.

Use `dpstat snapshot` to read every telemetry input in one JSON document:

```bash
sudo ./build/dpstat snapshot --json
sudo ./build/dpstat snapshot --json --ifindex <ingress-ifindex>
```

The output includes `ts_ns`, `active.slot`, `active.version`, XDP mode and
program metadata, node counters, sample and bloom statistics, and a sorted
`services` array. Each service row has `dp_id`, clean/drop packets and bytes,
and `drop_by_reason`. Provide the ingress ifindex to report the live XDP mode;
without it, the mode remains `unknown`. If a required pinned map is unavailable,
`dpstat` reports the gateway as offline and exits non-zero rather than emitting
a partial snapshot.

The same JSON document includes `node_control.bypass` and
`bypass.{pkts,bytes}`. `set-bypass 1` makes valid parsed IPv4 traffic redirect
without entering the service-policy pipeline; it does not forward IPv6,
malformed IPv4, or fragments. Bypass bytes are counted separately and do not
increment per-service clean counters.

## Apply helper (xdpgw-apply)

`xdpgw-apply` is the write side of the control plane → data plane apply path. The loader pins the slotted
config maps (`service_map`, `rule_block_map`, `whitelist_bloom`/`_lpm`, `vip_config_map`,
`global_blacklist_bloom`/`_lpm`, `udp_blocked_port_bitmap`, `fair_config_map`, `fair_node_config`,
`gbl_meta`, `active_config`) under `/sys/fs/bpf/xdp_gateway/`;
runtime-state maps, the static inner maps, and `tx_devmap` stay owned by the loader and are never opened
here.

On a committed change the worker serializes a consistent full-node snapshot in the `apply_snapshot.h`
wire format and runs the helper:

```bash
sudo ./build/xdpgw-apply <snapshot-path>
```

The helper opens the pinned config maps and, for the **inactive** slot, builds a fresh inner per
service-scoped outer from the snapshot (reusing the loader's seed/leaf-writer idioms), carries forward
the feed-owned global-deny inners unchanged, structurally verifies the slot, then performs a single
`active_config` write to flip the active slot and bump the version. It prints the slot and version
transition and exits `0` on success. Any build, verify, or carry-forward failure exits non-zero **before**
the flip, so the last-good slot stays live — abort-before-flip is the entire rollback. No BPF work runs
in the worker; all map writes stay in this audited C helper.

Inspect the live slot and version with `sudo ./build/dpstat active_config`. Build the helper and run its
snapshot parse self-test with `make apply`; exercise the full privileged flow with `sudo make applybulk`
(1000-service build/verify/flip under a 5 s budget) and the apply smoke inside `sudo make smoke`.

### Global-deny mode

The same binary has two modes, distinguished by the snapshot `kind`
(`SERVICE_FULL` or `GLOBAL_DENY`; `apply_snapshot.h` schema **v2**). The threat
feed worker serializes a `GLOBAL_DENY` snapshot — the sorted, deduplicated union
of manual and feed global-deny CIDRs plus the desired revision — and runs the
helper the same way:

```bash
sudo ./build/xdpgw-apply <global-deny-snapshot-path>
```

Global mode is the **inverse carry-forward** of service mode. It rebuilds only
the feed-owned global-deny maps (`global_blacklist_bloom`/`_lpm` with the AD-023
`/24` bloom expansion and `GBL_F_HAS_BROAD` escape, plus a coherent inactive
`gbl_meta`) into the inactive slot from the snapshot, and **pointer-carries every
service-scoped outer** (`service_map`, `rule_block_map`, `whitelist_*`,
`vip_config_map`, `service_blacklist_*`, `fair_config_map`), `fair_node_config`,
and `udp_blocked_port_bitmap` unchanged — so a global-deny apply never disturbs
service, rule, whitelist, service-blacklist, fairness, or bitmap behavior.

**Shared lock.** Both modes acquire one exclusive `flock` on the pin directory
before fresh-reading `active_config`, and hold it through verify and commit, so a
service apply and a global-deny apply can never race on the slot.

**No-flip rollback and version semantics.** The helper structurally verifies the
inactive slot (carried inner IDs, inserted count, bloom fill/broad policy, meta
flags) and then performs the **single** `active_config` write that flips the slot
and bumps the node map version. Any build, verify, or timeout failure exits
non-zero **before** the flip, leaving the prior slot, version, and verdicts live.
Service and global applies share this one `active_config`/version and alternate
safely; each re-reads the live slot fresh, so neither writes stale-over-new.

The control plane skips invoking the helper when the desired revision already
equals the active one; an identical-but-unconverged desired state (e.g. after a
failed apply) still runs and converges.

**Test and scale commands** (root required):

```bash
sudo make globalapplysmoke   # feed snapshot → real helper → blacklist_drop verdict
sudo make globalapplyscale   # loads 1,048,576 entries; rejects 1,048,577 before flip
```

The 1M envelope reuses the AD-023 1M LPM / 2M bloom limits; a snapshot above
1,048,576 distinct entries is rejected before any map write.

## Requirements

- `clang`
- `llvm-strip`
- `bpftool`
- `libbpf` headers
- kernel UAPI headers
