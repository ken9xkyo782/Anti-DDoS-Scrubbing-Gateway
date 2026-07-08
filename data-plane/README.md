# Data Plane

Native-XDP packet parsing and fail-fast verdict code lives here.

## Build

```bash
make bpf skel loader
```

This compiles `src/xdp_gateway.bpf.c` with `clang -target bpf` and generates a libbpf skeleton at
`build/xdp_gateway.skel.h`. The loader binary is written to `build/xdp_gateway_loader`.

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

For a demo service before the M4 worker exists, set `SERVICE_DEST` to an IPv4 address or canonical CIDR.
An address without a prefix is seeded as `/32`; invalid, IPv6, or non-canonical CIDRs are rejected.
Without `SERVICE_DEST`, `active_config` is still seeded and the service map stays empty, so valid IPv4
traffic fails closed as `service_miss`.

```bash
SERVICE_DEST=10.0.0.2 sudo ./build/xdp_gateway_loader <in-veth> <out-veth>
SERVICE_DEST=10.0.0.0/24 make run IFACE=<in-veth> OUT=<out-veth>
```

## Requirements

- `clang`
- `llvm-strip`
- `bpftool`
- `libbpf` headers
- kernel UAPI headers
