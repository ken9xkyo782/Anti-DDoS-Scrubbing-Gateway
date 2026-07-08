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
Without `SERVICE_DEST`, `active_config` is still seeded and the service map stays empty, so valid IPv4
traffic fails closed as `service_miss`.

```bash
SERVICE_DEST=10.0.0.2 sudo ./build/xdp_gateway_loader <in-veth> <out-veth>
SERVICE_DEST=10.0.0.0/24 make run IFACE=<in-veth> OUT=<out-veth>
```

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
drop counters stay correct even when samples are suppressed or lost.

## Requirements

- `clang`
- `llvm-strip`
- `bpftool`
- `libbpf` headers
- kernel UAPI headers
