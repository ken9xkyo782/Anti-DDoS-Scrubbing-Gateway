# Data Plane

Native-XDP packet parsing and fail-fast verdict code lives here.

## Build

```bash
make bpf skel loader
```

This compiles `src/xdp_gateway.bpf.c` with `clang -target bpf` and generates a libbpf skeleton at
`build/xdp_gateway.skel.h`. The loader binary is written to `build/xdp_gateway_loader`.

## Native XDP Loader

Run the loader with an interface argument or `IN_IFACE`:

```bash
sudo ./build/xdp_gateway_loader <veth>
IN_IFACE=<veth> sudo ./build/xdp_gateway_loader
make run IFACE=<veth>
```

The loader attaches with `XDP_FLAGS_DRV_MODE` only. If the driver does not support native XDP, it exits
with a clear native-unsupported error and does not fall back to generic/SKB mode. Press Ctrl-C to detach;
`ip link show <veth>` should then show no XDP program on the interface.

## Requirements

- `clang`
- `llvm-strip`
- `bpftool`
- `libbpf` headers
- kernel UAPI headers
