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
- `XDPGW_SEED_SBL_CIDR=<source-cidr>` seeds one service-scoped blacklist CIDR in slot 0.
  Set `SERVICE_DEST` with this variable so the loader can mark the demo service active.
- `XDPGW_SEED_BLOCKED_PORT=<udp-source-port>` sets one slot-0 UDP blocked-port bitmap bit.

The loader seed path is intentionally small. It writes one CIDR or port for smoke testing and leaves
the 16..23 global-bloom expansion band, snapshot replacement, and bulk map population to the M4
builder.

## Allow rules and tunnel traffic

`src/rules.h` is the M4 map-build contract for allow rules. Rule blocks must be pre-sorted by ascending
priority, because the hot path treats array position as first-match order. The `bps` field is bytes per
second; the future worker must convert any control-plane unit before writing the map.

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

`src/blacklist.h` is the M4 map-build contract for global and service-scoped blacklist maps. The deny
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

Use `dpstat` while the loader is running:

```bash
sudo ./build/dpstat counters
sudo ./build/dpstat counters -w 2
sudo ./build/dpstat tail
sudo ./build/dpstat rate 256 64
```

Counter maps reset when the XDP program is reloaded. Consumers must compute deltas between reads, not
interpret values as lifetime totals. Sampling budget is per CPU; a rate of `256` permits up to
`256 * online_cpu_count` events per second across the node, with burst `64` per CPU by default. Exact
drop counters stay correct even when samples are suppressed or lost. `dpstat counters` also prints
the `bloom_hit_lpm_miss` rows for whitelist, global blacklist, service blacklist, and total.

## Apply helper (xdpgw-apply)

`xdpgw-apply` is the write side of the control plane → data plane apply path. The loader pins the 14
slotted config maps (`service_map`, `rule_block_map`, `whitelist_bloom`/`_lpm`, `vip_config_map`,
`global_blacklist_bloom`/`_lpm`, `service_blacklist_bloom`/`_lpm`, `udp_blocked_port_bitmap`,
`fair_config_map`, `fair_node_config`, `gbl_meta`, `active_config`) under `/sys/fs/bpf/xdp_gateway/`;
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

## Requirements

- `clang`
- `llvm-strip`
- `bpftool`
- `libbpf` headers
- kernel UAPI headers
